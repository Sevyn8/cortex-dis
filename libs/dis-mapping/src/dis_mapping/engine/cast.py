"""The cast sub-stage: canonical string representation -> target type.

Runs AFTER normalize (D20; the ordering is load-bearing): normalize produced
canonical representations (ISO dates/datetimes, ``.``-decimal numbers,
``true``/``false`` booleans), so the cast is mechanical. A value the target type
still refuses is the same per-cell failure shape as a normalization failure, with
``stage="cast"`` (architecture.md §6.1 step 6: rows are exploded into candidates
only for rows that pass normalization AND cast).

Cells already nulled by a normalize-step failure pass through as null and are NOT
re-reported here (failure masks only fire where the input was non-null).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from dis_mapping.models.transform import CastSpec
from dis_mapping.result import CellNormalizationFailure


def target_dtype(spec: CastSpec) -> pl.DataType:
    """The polars dtype a CastSpec targets."""
    if spec.type == "string":
        return pl.String()
    if spec.type == "integer":
        return pl.Int64()
    if spec.type == "decimal":
        # precision/scale mandatory for decimal (validated at construction).
        assert spec.precision is not None and spec.scale is not None  # noqa: S101 — type narrowing
        return pl.Decimal(precision=spec.precision, scale=spec.scale)
    if spec.type == "date":
        return pl.Date()
    if spec.type == "datetime":
        return pl.Datetime(time_unit="us", time_zone="UTC")
    return pl.Boolean()


def _cast_string_series(series: pl.Series, spec: CastSpec) -> pl.Series:
    """Cast a canonical-string column to the target dtype; unparseable -> null."""
    if spec.type == "string":
        return series
    if spec.type == "integer":
        return series.cast(pl.Int64, strict=False)
    if spec.type == "decimal":
        return series.cast(target_dtype(spec), strict=False)
    if spec.type == "date":
        return series.str.to_date(format="%Y-%m-%d", strict=False)
    if spec.type == "datetime":
        return series.str.to_datetime(time_zone="UTC", strict=False)
    # boolean: normalize's parse_boolean emits canonical "true"/"false".
    frame = series.to_frame()
    return frame.select(
        pl.when(pl.col(series.name) == "true")
        .then(pl.lit(value=True))
        .when(pl.col(series.name) == "false")
        .then(pl.lit(value=False))
        .otherwise(pl.lit(None, dtype=pl.Boolean))
        .alias(series.name)
    ).to_series()


def _expected_format(spec: CastSpec) -> str:
    if spec.type == "decimal":
        return f"decimal({spec.precision},{spec.scale})"
    if spec.type == "date":
        return "ISO date (%Y-%m-%d)"
    if spec.type == "datetime":
        return "ISO-8601 UTC datetime"
    if spec.type == "boolean":
        return "'true' or 'false'"
    return spec.type


def run_cast(
    frame: pl.DataFrame,
    cast_rules: Mapping[str, CastSpec],
    rename_inverse: Mapping[str, str],
    failures: list[CellNormalizationFailure],
) -> pl.DataFrame:
    """Run the cast sub-stage over every column with a declared target type."""
    for column, spec in cast_rules.items():
        series = frame.get_column(column)
        target = target_dtype(spec)
        if series.dtype == target:
            continue  # already typed (e.g. an API channel handing typed columns)
        if series.dtype == pl.String:
            cast_values = _cast_string_series(series, spec)
        else:
            cast_values = series.cast(target, strict=False)
        failed = cast_values.is_null() & series.is_not_null()
        if failed.any():
            expected = _expected_format(spec)
            for row in failed.arg_true().to_list():
                raw: Any = series[row]
                failures.append(
                    CellNormalizationFailure(
                        row_index=row,
                        column=column,
                        source_column=rename_inverse.get(column),
                        value=None if raw is None else str(raw),
                        op="cast",
                        transform_index=0,
                        expected_format=expected,
                        stage="cast",
                        reason=(f"column {column!r}: value could not be cast; expected {expected}"),
                    )
                )
        frame = frame.with_columns(cast_values.rename(column))
    return frame
