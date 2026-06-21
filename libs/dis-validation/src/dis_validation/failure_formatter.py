"""Failure formatting: Pandera raw failure cases -> typed, tenant-readable reasons.

The three failure types stay DISTINCT (D18/D20): source-shape and canonical-shape
failures are built here; normalization failures originate in ``dis-mapping``
(``CellNormalizationFailure``) and are never re-detected or re-formatted by this
lib.

The failure objects may carry the offending value (``value``) — that is the
quarantine payload the console needs. It is NEVER logged by this lib; log lines
carry column/check names and counts only (root CLAUDE.md: no PII, no raw
payloads).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class SourceShapeFailure:
    """A pre-mapping (source-shape) failure, in the tenant's vocabulary."""

    column: str | None
    check: str
    row_index: int | None
    value: str | None
    reason: str


@dataclass(frozen=True)
class CanonicalShapeFailure:
    """A post-mapping (canonical-shape) failure on a mapped contribution."""

    column: str | None
    check: str
    row_index: int | None
    value: str | None
    reason: str


def _cell(row: dict[str, object], key: str) -> str | None:
    raw = row.get(key)
    return None if raw is None else str(raw)


def _row_index(row: dict[str, object]) -> int | None:
    raw = row.get("index")
    return None if raw is None else int(str(raw))


def format_source_shape_failures(
    failure_cases: pl.DataFrame, observed_columns: Sequence[str], expected_columns: Sequence[str]
) -> tuple[SourceShapeFailure, ...]:
    """Build tenant-readable source-shape failures from Pandera failure cases.

    The "expected column ``item_code``, got ``itemcd``" reason pairs a missing
    expected column (``column_in_dataframe``) with the chunk's unexpected columns,
    which Pandera alone does not relate — hence ``observed_columns``.
    """
    unexpected = [name for name in observed_columns if name not in set(expected_columns)]
    failures: list[SourceShapeFailure] = []
    for row in failure_cases.iter_rows(named=True):
        check = str(row.get("check"))
        value = _cell(row, "failure_case")
        column = _cell(row, "column")
        if str(row.get("schema_context")) == "DataFrameSchema":
            # Frame-level checks report the schema name in the column slot.
            column = None
        if check == "column_in_dataframe":
            # The expected column named by failure_case is absent from the chunk.
            got = ", ".join(repr(name) for name in unexpected) if unexpected else "no candidate column"
            reason = f"expected column {value!r}, got {got}"
            failures.append(
                SourceShapeFailure(column=value, check=check, row_index=None, value=None, reason=reason)
            )
        elif check == "column_in_schema":
            reason = f"unexpected column {value!r} is not in this source's declared vocabulary"
            failures.append(
                SourceShapeFailure(column=value, check=check, row_index=None, value=None, reason=reason)
            )
        elif check.startswith("row_count"):
            reason = f"chunk row count is implausible: expected {check.removeprefix('row_count')}"
            failures.append(
                SourceShapeFailure(column=None, check=check, row_index=None, value=None, reason=reason)
            )
        elif check.startswith("max_null_fraction"):
            reason = f"column {column!r} has more nulls than the declared bound ({check})"
            failures.append(
                SourceShapeFailure(column=column, check=check, row_index=None, value=None, reason=reason)
            )
        else:
            index = _row_index(row)
            at = f" (row {index})" if index is not None else ""
            reason = f"column {column!r}: value failed {check}{at}"
            failures.append(
                SourceShapeFailure(column=column, check=check, row_index=index, value=value, reason=reason)
            )
    return tuple(failures)


def format_canonical_shape_failures(
    failure_cases: pl.DataFrame,
) -> tuple[CanonicalShapeFailure, ...]:
    """Build typed canonical-shape failures (per-row grain where Pandera gives it)."""
    failures: list[CanonicalShapeFailure] = []
    for row in failure_cases.iter_rows(named=True):
        check = str(row.get("check"))
        value = _cell(row, "failure_case")
        column = _cell(row, "column")
        if str(row.get("schema_context")) == "DataFrameSchema":
            # Frame-level (cross-field) checks report the SCHEMA name in the
            # column slot — that is not a data column.
            column = None
        index = _row_index(row)
        if check == "column_in_dataframe":
            reason = f"contribution is missing owned column {value!r}"
            failures.append(
                CanonicalShapeFailure(column=value, check=check, row_index=None, value=None, reason=reason)
            )
        elif check == "column_in_schema":
            reason = f"contribution carries column {value!r} outside the source-owned, mapping-produced set"
            failures.append(
                CanonicalShapeFailure(column=value, check=check, row_index=None, value=None, reason=reason)
            )
        elif column is None:
            at = f" at row {index}" if index is not None else ""
            reason = f"cross-field invariant {check!r} failed{at}"
            failures.append(
                CanonicalShapeFailure(column=None, check=check, row_index=index, value=value, reason=reason)
            )
        elif check == "not_nullable":
            # The mandatory-field product rule, in words both audiences read:
            # the column is required (model nullability) but the value is missing.
            at = f" (row {index})" if index is not None else ""
            reason = f"column {column!r} is required but arrived empty{at}"
            failures.append(
                CanonicalShapeFailure(column=column, check=check, row_index=index, value=None, reason=reason)
            )
        else:
            at = f" at row {index}" if index is not None else ""
            reason = f"column {column!r} failed {check!r}{at}"
            failures.append(
                CanonicalShapeFailure(column=column, check=check, row_index=index, value=value, reason=reason)
            )
    return tuple(failures)
