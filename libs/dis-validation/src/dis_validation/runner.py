"""Suite runner: pure validation of an in-memory frame against a materialized suite.

Takes data and a definition handed in by the caller and returns pass or typed
failures — no DB access, no config read (resolving which suite version is active
and fetching it from ``config.source_mappings`` is the consumer's side-input,
Slice 10). The two entry points keep the two failure types distinct (D18).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandera.errors
import pandera.polars as pa
import polars as pl

from dis_core.errors import SuiteDefinitionError
from dis_core.logging import LogContext, get_logger
from dis_validation.canonical_shape import CanonicalShapeSuiteDef, materialize_canonical_shape
from dis_validation.failure_formatter import (
    CanonicalShapeFailure,
    SourceShapeFailure,
    format_canonical_shape_failures,
    format_source_shape_failures,
)
from dis_validation.source_shape import SourceShapeSuiteDef, materialize_source_shape


@dataclass(frozen=True)
class SourceShapeResult:
    passed: bool
    failures: tuple[SourceShapeFailure, ...] = ()


@dataclass(frozen=True)
class CanonicalShapeResult:
    passed: bool
    failures: tuple[CanonicalShapeFailure, ...] = ()


def _logger(stage: str, log_context: LogContext | None) -> object:
    return get_logger(
        "dis-validation",
        stage=stage,
        tenant_id=log_context.tenant_id if log_context else None,
        trace_id=log_context.trace_id if log_context else None,
    )


def run_source_shape(
    definition: SourceShapeSuiteDef,
    chunk: pl.DataFrame,
    *,
    log_context: LogContext | None = None,
) -> SourceShapeResult:
    """Judge a raw chunk in the tenant's vocabulary; pass or typed failures."""
    schema = materialize_source_shape(definition)
    try:
        schema.validate(chunk, lazy=True)
    except pandera.errors.SchemaErrors as exc:
        failures = format_source_shape_failures(
            exc.failure_cases,
            observed_columns=chunk.columns,
            expected_columns=[c.name for c in definition.expected_columns],
        )
        log = _logger("validate.source_shape", log_context)
        log.warning(  # type: ignore[attr-defined]
            "source-shape suite failed",
            extra={
                "failures": len(failures),
                "checks": sorted({f.check for f in failures}),
                "columns": sorted({f.column for f in failures if f.column is not None}),
            },
        )
        return SourceShapeResult(passed=False, failures=failures)
    return SourceShapeResult(passed=True)


# Schema of pandera's polars failure_cases frame (mirrored by the D50 synthesis).
_FAILURE_CASES_SCHEMA: dict[str, pl.DataType] = {
    "failure_case": pl.String(),
    "schema_context": pl.String(),
    "column": pl.String(),
    "check": pl.String(),
    "check_number": pl.Int32(),
    "index": pl.Int32(),
}


def _decimal_dtype_precheck(
    schema: pa.DataFrameSchema, contribution: pl.DataFrame
) -> tuple[pa.DataFrameSchema, pl.DataFrame | None]:
    """D50 workaround — scoped STRICTLY to Decimal-schema columns.

    pandera 0.31.1's polars engine crashes with a raw ``AssertionError``
    (``polars_engine.py``: "The return is expected to be of Decimal class") when a
    schema column declaring ``pl.Decimal`` meets NON-Decimal data, instead of
    reporting a dtype failure case (Decimal-vs-Decimal precision/scale mismatches
    report natively; so does every non-Decimal dtype). Until the upstream fix
    (removal trigger: the canary test in
    tests/unit/test_pandera_decimal_canary.py goes red), this pre-check detects
    exactly that case, synthesizes the SAME failure-case row a native dtype check
    produces (same check string, same column-level grain — formatted by the same
    formatter, so downstream sees one shape), and neutralizes only the affected
    column for the pandera run. Every other column stays entirely pandera's; no
    AssertionError is caught anywhere.
    """
    synthesized: list[dict[str, str | int | None]] = []
    columns = dict(schema.columns)
    for name, column in schema.columns.items():
        expected = column.dtype.type if column.dtype is not None else None
        if not isinstance(expected, pl.Decimal) or name not in contribution.columns:
            continue
        actual = contribution.schema[name]
        if isinstance(actual, pl.Decimal):
            continue  # native pandera handles Decimal-vs-Decimal correctly
        synthesized.append(
            {
                "failure_case": str(actual),
                "schema_context": "Column",
                "column": name,
                "check": f"dtype('{expected}')",
                "check_number": None,
                "index": None,
            }
        )
        # Presence still checked; dtype/value checks are meaningless on a column
        # whose dtype failure is already recorded.
        columns[name] = pa.Column(None, nullable=True, required=True, name=name)
    if not synthesized:
        return schema, None
    neutralized = pa.DataFrameSchema(columns, checks=list(schema.checks), strict=True, name=schema.name)
    return neutralized, pl.DataFrame(synthesized, schema=_FAILURE_CASES_SCHEMA)


def run_canonical_shape(
    definition: CanonicalShapeSuiteDef,
    contribution: pl.DataFrame,
    *,
    log_context: LogContext | None = None,
) -> CanonicalShapeResult:
    """Judge a mapped contribution against the invariants of the columns it owns."""
    schema = materialize_canonical_shape(definition)
    schema, synthesized_cases = _decimal_dtype_precheck(schema, contribution)
    try:
        schema.validate(contribution, lazy=True)
    except pandera.errors.SchemaErrors as exc:
        cases = exc.failure_cases
        if synthesized_cases is not None:
            cases = pl.concat([synthesized_cases, cases.select(_FAILURE_CASES_SCHEMA.keys())])
        failures = format_canonical_shape_failures(cases)
        log = _logger("validate.canonical_shape", log_context)
        log.warning(  # type: ignore[attr-defined]
            "canonical-shape suite failed",
            extra={
                "target_model": definition.target_model.__name__,
                "failures": len(failures),
                "checks": sorted({f.check for f in failures}),
                "columns": sorted({f.column for f in failures if f.column is not None}),
            },
        )
        return CanonicalShapeResult(passed=False, failures=failures)
    except pandera.errors.SchemaError as exc:  # pragma: no cover - lazy=True yields SchemaErrors
        raise SuiteDefinitionError(
            f"suite for {definition.target_model.__name__} could not run: {exc}",
            model=definition.target_model.__name__,
        ) from exc
    if synthesized_cases is not None:
        # pandera passed everything it ran, but the D50 pre-check found Decimal
        # dtype mismatches — the contribution fails on those.
        failures = format_canonical_shape_failures(synthesized_cases)
        log = _logger("validate.canonical_shape", log_context)
        log.warning(  # type: ignore[attr-defined]
            "canonical-shape suite failed",
            extra={
                "target_model": definition.target_model.__name__,
                "failures": len(failures),
                "checks": sorted({f.check for f in failures}),
                "columns": sorted({f.column for f in failures if f.column is not None}),
            },
        )
        return CanonicalShapeResult(passed=False, failures=failures)
    return CanonicalShapeResult(passed=True)
