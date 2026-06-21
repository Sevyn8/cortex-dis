"""Transform and cast specs — the bounded declarative vocabulary (slice-05 OQ3).

Ops are ATOMIC and SINGLE-PURPOSE. A column carries an ORDERED LIST of
``TransformSpec`` applied in declared sequence (e.g. ``normalize_whitespace`` then
``normalize_case``); list order is significant and tenant-declared. It sits one
level under the mandatory ``rename -> normalize -> cast -> derive`` stage ordering
(D20). An empty list is a valid no-op.

Locale rule (no doc home exists in the repo; pinned here and in this lib's
CLAUDE.md): ``parse_decimal``'s ``decimal_separator`` and ``thousands_separator``,
and ``parse_integer``'s ``thousands_separator``, are MANDATORY declarations —
never defaulted, never inferred. The key must be present; ``thousands_separator``
may be an explicit JSON ``null`` meaning "this source uses no thousands
separator" (still a declaration). Ambiguity like ``"1,299.50"`` vs ``"1.299,50"``
is resolved by declaration only. A missing declaration raises
:class:`~dis_core.errors.MappingConfigError` at construction, never at runtime.

Validation here is *config* validation (code-quality rule 4): per-cell data
failures at runtime are typed result objects (``CellNormalizationFailure``),
never exceptions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from dis_core.errors import MappingConfigError

# -- The bounded vocabulary ------------------------------------------------------
# Normalize ops: str -> canonical-str representation (cast converts type after;
# normalize-before-cast is load-bearing, D20). All ops pass null through untouched,
# which is what lets a cell that failed at step k skip the remaining steps.
NORMALIZE_OPS: frozenset[str] = frozenset(
    {
        "parse_date",
        "parse_datetime",
        "parse_decimal",
        "parse_percent",
        "parse_integer",
        "parse_boolean",
        "map_enum",
        "null_tokens",
        "normalize_whitespace",
        "normalize_case",
    }
)

# Derive generators: produce a derive target's initial value (derive is bounded to
# the same declarative vocabulary as normalize — no arbitrary logic; slice-05).
DERIVE_GENERATOR_OPS: frozenset[str] = frozenset({"copy", "constant", "date_from_datetime"})

CastType = Literal["string", "integer", "decimal", "date", "datetime", "boolean"]


class TransformSpec(BaseModel):
    """One atomic op + its declared args. ``extra="forbid"``: typo'd keys fail loud."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: str
    args: dict[str, Any] = {}


class CastSpec(BaseModel):
    """Target type for the cast sub-stage. ``decimal`` requires declared precision/scale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: CastType
    # decimal only — mandatory for decimal, forbidden otherwise (validated in
    # validate_cast_spec; kept flat so the JSONB shape stays simple).
    precision: int | None = None
    scale: int | None = None


def _require_keys(
    op: str, column: str, args: Mapping[str, Any], required: frozenset[str], optional: frozenset[str]
) -> None:
    """Exact-key discipline: required keys present, nothing outside required|optional."""
    missing = required - set(args)
    if missing:
        raise MappingConfigError(
            f"op {op!r} on column {column!r} is missing required arg(s) {sorted(missing)}; "
            "args are declared, never defaulted or inferred",
            column=column,
        )
    unknown = set(args) - required - optional
    if unknown:
        raise MappingConfigError(
            f"op {op!r} on column {column!r} has unknown arg(s) {sorted(unknown)}",
            column=column,
        )


def _require_str(op: str, column: str, args: Mapping[str, Any], key: str) -> str:
    value = args[key]
    if not isinstance(value, str) or not value:
        raise MappingConfigError(
            f"op {op!r} on column {column!r}: arg {key!r} must be a non-empty string",
            column=column,
        )
    return value


def _require_str_list(op: str, column: str, args: Mapping[str, Any], key: str) -> list[str]:
    value = args[key]
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise MappingConfigError(
            f"op {op!r} on column {column!r}: arg {key!r} must be a non-empty list of strings",
            column=column,
        )
    return value


def validate_normalize_args(spec: TransformSpec, column: str) -> None:
    """Validate one normalize-vocabulary op's args. Raises MappingConfigError."""
    op, args = spec.op, spec.args
    if op == "parse_date":
        _require_keys(op, column, args, frozenset({"format"}), frozenset())
        _require_str(op, column, args, "format")
    elif op == "parse_datetime":
        _require_keys(op, column, args, frozenset({"format", "timezone"}), frozenset())
        fmt = _require_str(op, column, args, "format")
        tz = args["timezone"]
        if tz is None:
            # Declared "the values carry their own offset": format must say so.
            if "%z" not in fmt:
                raise MappingConfigError(
                    f"op 'parse_datetime' on column {column!r}: timezone is null but format "
                    f"{fmt!r} carries no %z offset — the zone must be declared, never guessed",
                    column=column,
                )
        elif not isinstance(tz, str) or not tz:
            raise MappingConfigError(
                f"op 'parse_datetime' on column {column!r}: timezone must be a non-empty "
                "string or an explicit null (offset-bearing format)",
                column=column,
            )
        elif "%z" in fmt:
            # Both an assumed zone AND a per-value offset is an ambiguous
            # declaration — which wins? Refuse rather than guess.
            raise MappingConfigError(
                f"op 'parse_datetime' on column {column!r}: timezone {tz!r} conflicts with "
                f"the %z offset in format {fmt!r}; declare one, not both",
                column=column,
            )
    elif op in ("parse_decimal", "parse_percent"):
        # Locale rule: BOTH separators are mandatory declarations. parse_percent takes
        # the same separator args as parse_decimal (it parses the numeric body identically,
        # then divides by 100); they validate by the same rules.
        _require_keys(op, column, args, frozenset({"decimal_separator", "thousands_separator"}), frozenset())
        dec = _require_str(op, column, args, "decimal_separator")
        if len(dec) != 1:
            raise MappingConfigError(
                f"op {op!r} on column {column!r}: decimal_separator must be one character",
                column=column,
            )
        thou = args["thousands_separator"]
        if thou is not None and (not isinstance(thou, str) or len(thou) != 1):
            raise MappingConfigError(
                f"op {op!r} on column {column!r}: thousands_separator must be one "
                "character or an explicit null (declared 'no thousands separator')",
                column=column,
            )
        if thou == dec:
            raise MappingConfigError(
                f"op {op!r} on column {column!r}: decimal and thousands separators must differ",
                column=column,
            )
    elif op == "parse_integer":
        _require_keys(op, column, args, frozenset({"thousands_separator"}), frozenset())
        thou = args["thousands_separator"]
        if thou is not None and (not isinstance(thou, str) or len(thou) != 1):
            raise MappingConfigError(
                f"op 'parse_integer' on column {column!r}: thousands_separator must be one "
                "character or an explicit null (declared 'no thousands separator')",
                column=column,
            )
    elif op == "parse_boolean":
        _require_keys(op, column, args, frozenset({"true_values", "false_values"}), frozenset())
        true_values = _require_str_list(op, column, args, "true_values")
        false_values = _require_str_list(op, column, args, "false_values")
        overlap = set(true_values) & set(false_values)
        if overlap:
            raise MappingConfigError(
                f"op 'parse_boolean' on column {column!r}: tokens {sorted(overlap)} appear in "
                "both true_values and false_values",
                column=column,
            )
    elif op == "map_enum":
        _require_keys(op, column, args, frozenset({"mapping"}), frozenset({"case_insensitive"}))
        mapping = args["mapping"]
        if (
            not isinstance(mapping, dict)
            or not mapping
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items())
        ):
            raise MappingConfigError(
                f"op 'map_enum' on column {column!r}: mapping must be a non-empty str->str dict",
                column=column,
            )
        if not isinstance(args.get("case_insensitive", False), bool):
            raise MappingConfigError(
                f"op 'map_enum' on column {column!r}: case_insensitive must be a bool",
                column=column,
            )
        if args.get("case_insensitive", False):
            folded = [k.upper() for k in mapping]
            if len(set(folded)) != len(folded):
                raise MappingConfigError(
                    f"op 'map_enum' on column {column!r}: mapping keys collide under "
                    "case_insensitive folding",
                    column=column,
                )
    elif op == "null_tokens":
        _require_keys(op, column, args, frozenset({"tokens"}), frozenset())
        _require_str_list(op, column, args, "tokens")
    elif op == "normalize_whitespace":
        _require_keys(op, column, args, frozenset(), frozenset({"trim", "collapse"}))
        if not isinstance(args.get("trim", True), bool) or not isinstance(args.get("collapse", True), bool):
            raise MappingConfigError(
                f"op 'normalize_whitespace' on column {column!r}: trim/collapse must be bools",
                column=column,
            )
    elif op == "normalize_case":
        _require_keys(op, column, args, frozenset({"mode"}), frozenset())
        if args["mode"] not in ("upper", "lower"):
            raise MappingConfigError(
                f"op 'normalize_case' on column {column!r}: mode must be 'upper' or 'lower'",
                column=column,
            )
    else:
        raise MappingConfigError(
            f"unknown normalize op {op!r} on column {column!r}; the vocabulary is "
            f"{sorted(NORMALIZE_OPS)} (bounded, declarative; extensions are a vocabulary "
            "change, not a per-source code path)",
            column=column,
        )


def validate_derive_generator_args(spec: TransformSpec, column: str) -> None:
    """Validate a derive generator op's args. Raises MappingConfigError."""
    op, args = spec.op, spec.args
    if op == "copy":
        _require_keys(op, column, args, frozenset({"source_column"}), frozenset())
        _require_str(op, column, args, "source_column")
    elif op == "constant":
        _require_keys(op, column, args, frozenset({"value"}), frozenset())
        if not isinstance(args["value"], str | int | float | bool):
            raise MappingConfigError(
                f"op 'constant' on column {column!r}: value must be a scalar (str/int/float/bool)",
                column=column,
            )
    elif op == "date_from_datetime":
        _require_keys(op, column, args, frozenset({"source_column"}), frozenset())
        _require_str(op, column, args, "source_column")
    else:
        raise MappingConfigError(
            f"unknown derive generator {op!r} on column {column!r}; a derive list must start "
            f"with one of {sorted(DERIVE_GENERATOR_OPS)} followed by normalize-vocabulary ops",
            column=column,
        )


def validate_cast_spec(spec: CastSpec, column: str) -> None:
    """Validate a CastSpec. Decimal precision/scale are mandatory declarations."""
    if spec.type == "decimal":
        if spec.precision is None or spec.scale is None:
            raise MappingConfigError(
                f"cast to decimal on column {column!r} requires declared precision and scale "
                "(never defaulted)",
                column=column,
            )
        if spec.precision < 1 or spec.scale < 0 or spec.scale > spec.precision:
            raise MappingConfigError(
                f"cast to decimal on column {column!r}: invalid precision/scale "
                f"({spec.precision},{spec.scale})",
                column=column,
            )
    elif spec.precision is not None or spec.scale is not None:
        raise MappingConfigError(
            f"cast to {spec.type!r} on column {column!r} takes no precision/scale",
            column=column,
        )
