"""The normalize sub-stage: ordered per-column transform lists over canonical strings.

Normalize canonicalizes REPRESENTATION (str -> canonical str); cast converts type
afterwards — the ordering is load-bearing, not stylistic (D20:
``cast("23,45", float)`` fails where ``cast(normalize("23,45"), float)`` succeeds).

Each column's transform list is applied SEQUENTIALLY IN DECLARED ORDER. A cell
that fails at step *k* is recorded with that step's ``op`` and ``transform_index``
and its value is nulled; every op passes null through untouched, which is what
"skips the remaining steps for that cell" means operationally. The whole row is
dropped from the contribution later (partial rows yield nothing; slice-05
criterion 3).

All ops are pure Series -> Series computations: no I/O of any kind.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import polars as pl

from dis_core.errors import MappingConfigError, MappingInputError
from dis_mapping.result import CellNormalizationFailure


@dataclass(frozen=True)
class OpOutcome:
    """One op's result over a column: new values, genuine-failure mask, expectation."""

    values: pl.Series
    failed: pl.Series  # Boolean; True only where the op genuinely failed (never on null input)
    expected: str


def _no_failures(s: pl.Series) -> pl.Series:
    return pl.repeat(False, s.len(), dtype=pl.Boolean, eager=True)


def _null_where(values: pl.Series, mask: pl.Series) -> pl.Series:
    """Return ``values`` with cells nulled where ``mask`` is True."""
    nulls = pl.Series(values.name, [None] * values.len(), dtype=values.dtype)
    return values.zip_with(~mask.fill_null(False), nulls)


def _parse_date(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    fmt: str = args["format"]
    parsed = s.str.strptime(pl.Date, format=fmt, strict=False)
    failed = parsed.is_null() & s.is_not_null()
    return OpOutcome(parsed.dt.strftime("%Y-%m-%d"), failed, f"date in format {fmt!r}")


def _parse_datetime(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    fmt: str = args["format"]
    tz: str | None = args["timezone"]
    parsed = s.str.strptime(pl.Datetime("us"), format=fmt, strict=False)
    try:
        if isinstance(parsed.dtype, pl.Datetime) and parsed.dtype.time_zone is not None:
            # Offset-bearing format (%z): already aware; normalize the zone to UTC.
            parsed = parsed.dt.convert_time_zone("UTC")
        else:
            # Naive wall time in the DECLARED zone (validation guarantees tz is not
            # None here). DST-ambiguous / non-existent wall times cannot be resolved
            # by declaration -> null, surfacing as per-cell failures (loud, per cell).
            parsed = parsed.dt.replace_time_zone(
                tz, ambiguous="null", non_existent="null"
            ).dt.convert_time_zone("UTC")
    except pl.exceptions.ComputeError as exc:
        # An unknown zone name is a CONFIG error (the declaration is wrong), not a
        # data failure; it surfaces on first use because zone validity is checked
        # by polars' compiled tz database (no Python-side file I/O in this lib).
        raise MappingConfigError(
            f"parse_datetime: timezone {tz!r} was not accepted: {exc}", column=s.name
        ) from exc
    failed = parsed.is_null() & s.is_not_null()
    expected = f"datetime in format {fmt!r}" + (f" in zone {tz}" if tz else " with offset")
    return OpOutcome(parsed.dt.strftime("%Y-%m-%dT%H:%M:%S%.6f+00:00"), failed, expected)


def _numeric_body_pattern(thousands_separator: str | None) -> str:
    """The integer-part pattern under a declared thousands separator.

    With a separator declared, grouping is strict (3-digit groups) — a separator
    in any other position means the value is NOT in the declared locale and must
    fail loud rather than silently parse to a wrong number (e.g. ``"1,299.50"``
    under an EU declaration must fail, not become 1.2995).
    """
    if thousands_separator:
        sep = re.escape(thousands_separator)
        return rf"(\d+|\d{{1,3}}(?:{sep}\d{{3}})+)"
    return r"\d+"


def _strip_separators(
    s: pl.Series, decimal_separator: str | None, thousands_separator: str | None
) -> pl.Series:
    cleaned = s
    if thousands_separator:
        cleaned = cleaned.str.replace_all(thousands_separator, "", literal=True)
    if decimal_separator and decimal_separator != ".":
        cleaned = cleaned.str.replace_all(decimal_separator, ".", literal=True)
    return cleaned


def _parse_decimal(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    dec: str = args["decimal_separator"]
    thou: str | None = args["thousands_separator"]
    # Validate the ORIGINAL string against the declared locale BEFORE stripping —
    # stripping first silently mis-parses other-locale values.
    pattern = rf"^[+-]?{_numeric_body_pattern(thou)}({re.escape(dec)}\d+)?$"
    ok = s.str.contains(pattern)
    failed = s.is_not_null() & ~ok.fill_null(False)
    cleaned = _strip_separators(s, dec, thou)
    expected = f"decimal with separator {dec!r}" + (f" and thousands {thou!r}" if thou else "")
    return OpOutcome(_null_where(cleaned, failed), failed, expected)


def _parse_percent(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    dec: str = args["decimal_separator"]
    thou: str | None = args["thousands_separator"]
    # Validate the ORIGINAL string (with an OPTIONAL trailing "%") against the declared
    # locale BEFORE stripping — same discipline as parse_decimal. The leading sign comes
    # from the body pattern, so "-12.5%" is valid. The "%" glyph is optional: the
    # declaration (not the glyph) marks the column a percentage, so a bare "12.5" parses too.
    pattern = rf"^[+-]?{_numeric_body_pattern(thou)}({re.escape(dec)}\d+)?%?$"
    ok = s.str.contains(pattern)
    failed = s.is_not_null() & ~ok.fill_null(False)
    body = s.str.replace(r"%$", "")
    cleaned = _strip_separators(body, dec, thou)
    # Divide by 100 LOSSLESSLY via Decimal (never float — 12.567/100 must be exact), then
    # trim the trailing zeros the fixed-scale cast leaves and any dangling ".". Stays
    # vectorized (Series-level), matching the other ops.
    divided = (
        (cleaned.cast(pl.Decimal(scale=18), strict=False) / 100)
        .cast(pl.String)
        .str.replace(r"0+$", "")
        .str.replace(r"\.$", "")
    )
    expected = f"percentage with decimal separator {dec!r}" + (f" and thousands {thou!r}" if thou else "")
    return OpOutcome(_null_where(divided, failed), failed, expected)


def _parse_integer(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    thou: str | None = args["thousands_separator"]
    pattern = rf"^[+-]?{_numeric_body_pattern(thou)}$"
    ok = s.str.contains(pattern)
    failed = s.is_not_null() & ~ok.fill_null(False)
    cleaned = _strip_separators(s, None, thou)
    expected = "integer" + (f" with thousands {thou!r}" if thou else "")
    return OpOutcome(_null_where(cleaned, failed), failed, expected)


def _parse_boolean(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    true_values: list[str] = args["true_values"]
    false_values: list[str] = args["false_values"]
    is_true = s.is_in(true_values)
    is_false = s.is_in(false_values)
    values = (
        s.to_frame()
        .select(
            pl.when(pl.col(s.name).is_in(true_values))
            .then(pl.lit("true"))
            .when(pl.col(s.name).is_in(false_values))
            .then(pl.lit("false"))
            .otherwise(pl.lit(None, dtype=pl.String))
            .alias(s.name)
        )
        .to_series()
    )
    failed = s.is_not_null() & ~(is_true | is_false)
    return OpOutcome(values, failed, f"boolean token in {true_values} or {false_values}")


def _map_enum(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    mapping: dict[str, str] = args["mapping"]
    case_insensitive: bool = bool(args.get("case_insensitive", False))
    if case_insensitive:
        lookup = {k.upper(): v for k, v in mapping.items()}
        keys = s.str.to_uppercase()
    else:
        lookup = dict(mapping)
        keys = s
    mapped = keys.replace_strict(lookup, default=None, return_dtype=pl.String).rename(s.name)
    failed = mapped.is_null() & s.is_not_null()
    return OpOutcome(mapped, failed, f"one of {sorted(mapping)}")


def _null_tokens(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    tokens: list[str] = args["tokens"]
    values = _null_where(s, s.is_in(tokens))
    return OpOutcome(values, _no_failures(s), "")


def _normalize_whitespace(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    values = s
    if bool(args.get("trim", True)):
        values = values.str.strip_chars()
    if bool(args.get("collapse", True)):
        values = values.str.replace_all(r"\s+", " ")
    return OpOutcome(values, _no_failures(s), "")


def _normalize_case(s: pl.Series, args: Mapping[str, Any]) -> OpOutcome:
    mode: str = args["mode"]
    values = s.str.to_uppercase() if mode == "upper" else s.str.to_lowercase()
    return OpOutcome(values, _no_failures(s), "")


# Implementation registry. A unit test asserts this key set equals the declared
# vocabulary (models.transform.NORMALIZE_OPS) so spec and impl cannot drift.
NORMALIZE_IMPLS: dict[str, Callable[[pl.Series, Mapping[str, Any]], OpOutcome]] = {
    "parse_date": _parse_date,
    "parse_datetime": _parse_datetime,
    "parse_decimal": _parse_decimal,
    "parse_percent": _parse_percent,
    "parse_integer": _parse_integer,
    "parse_boolean": _parse_boolean,
    "map_enum": _map_enum,
    "null_tokens": _null_tokens,
    "normalize_whitespace": _normalize_whitespace,
    "normalize_case": _normalize_case,
}


def apply_transform_list(
    series: pl.Series,
    column: str,
    specs: list[Any],  # list[TransformSpec]; Any avoids a models import cycle in type position
    *,
    source_column: str | None,
    stage: Literal["normalize", "derive"],
    transform_index_offset: int,
    failures: list[CellNormalizationFailure],
) -> pl.Series:
    """Apply one column's ordered transform list, collecting per-cell failures.

    A cell failing at step *k* is recorded with that step's op and
    ``transform_index`` (offset by ``transform_index_offset`` so derive lists count
    the generator as step 0) and nulled — every op passes null through, so the
    remaining steps are skipped for that cell.
    """
    current = series
    for position, spec in enumerate(specs):
        entering = current
        outcome = NORMALIZE_IMPLS[spec.op](current, spec.args)
        failed = outcome.failed.fill_null(False)
        if failed.any():
            for row in failed.arg_true().to_list():
                raw = entering[row]
                failures.append(
                    CellNormalizationFailure(
                        row_index=row,
                        column=column,
                        source_column=source_column,
                        value=None if raw is None else str(raw),
                        op=spec.op,
                        transform_index=transform_index_offset + position,
                        expected_format=outcome.expected,
                        stage=stage,
                        reason=(
                            f"column {column!r}: step {transform_index_offset + position} "
                            f"({spec.op}) could not normalize the value; expected "
                            f"{outcome.expected}"
                        ),
                    )
                )
        current = _null_where(outcome.values, failed).rename(column)
    return current


def run_normalize(
    frame: pl.DataFrame,
    normalize_rules: Mapping[str, list[Any]],
    rename_inverse: Mapping[str, str],
    failures: list[CellNormalizationFailure],
) -> pl.DataFrame:
    """Run the normalize sub-stage over every column with declared rules."""
    for column, specs in normalize_rules.items():
        series = frame.get_column(column)
        if series.dtype != pl.String:
            # Normalize canonicalizes string representations; a typed column with
            # declared normalize rules is a caller-contract mismatch, not data.
            raise MappingInputError(
                f"column {column!r} has declared normalize rules but dtype "
                f"{series.dtype} (normalize operates on strings)",
                column=column,
            )
        normalized = apply_transform_list(
            series,
            column,
            specs,
            source_column=rename_inverse.get(column),
            stage="normalize",
            transform_index_offset=0,
            failures=failures,
        )
        frame = frame.with_columns(normalized)
    return frame
