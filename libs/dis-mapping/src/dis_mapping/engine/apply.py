"""``apply_mapping`` — the pure four-sub-stage engine (slice-05; D4, D8, D20).

A pure function over ``(mapping, chunk)``: no Postgres, GCS, Pub/Sub, network, or
file I/O. The same call wraps in today's container consumer loop and a future
Beam DoFn (D4's runner-swap guarantee rests on this purity).

What it produces: a PARTIAL canonical contribution — the source-owned,
mapping-produced columns only (rename targets + derive targets). What it never
produces: ``tenant_id`` / ``store_id`` / ``trace_id`` / ``mapping_version_id``
(consumer-injected after the engine runs; hard rule 5, D8, D22) — the engine has
no parameters from which it could populate them.

Failure semantics: per-cell, typed, returned as data alongside the rows that
succeeded. A row with ANY failed cell is dropped whole (no nulled-cell
pass-through). No pass-threshold is applied and nothing is routed — B2 (threshold
and chunk-vs-row routing) is the consumer's, Slice 10; ``row_index`` on every
failure keeps both routings implementable there.
"""

from __future__ import annotations

import polars as pl

from dis_core.logging import get_logger
from dis_mapping.engine.cast import run_cast
from dis_mapping.engine.derive import run_derive
from dis_mapping.engine.normalize import run_normalize
from dis_mapping.engine.rename import run_rename
from dis_mapping.models.source_mapping import SourceMapping
from dis_mapping.result import CellNormalizationFailure, LogContext, MappingResult


def apply_mapping(
    mapping: SourceMapping,
    chunk: pl.DataFrame,
    *,
    log_context: LogContext | None = None,
) -> MappingResult:
    """Apply ``mapping`` to a parsed in-memory ``chunk``; return the contribution.

    ``log_context`` (optional ``tenant_id``/``trace_id``) is used ONLY to bind log
    fields; it never enters the output frame. Log lines carry column/op names and
    counts — never a cell value (root CLAUDE.md: no PII, no raw payloads).
    """
    log = get_logger(
        "dis-mapping",
        stage="mapping",
        tenant_id=log_context.tenant_id if log_context else None,
        trace_id=log_context.trace_id if log_context else None,
    )

    failures: list[CellNormalizationFailure] = []
    rename_inverse = {canonical: source for source, canonical in mapping.rename.items()}

    # The four sub-stages, in the mandatory order (D20). No stage filters or
    # reorders rows, so a frame position IS the input-chunk row index throughout.
    frame = run_rename(chunk, mapping.rename)
    frame = run_normalize(frame, mapping.normalize, rename_inverse, failures)
    frame = run_cast(frame, mapping.cast, rename_inverse, failures)
    frame = run_derive(frame, mapping.derive, failures)

    failed_rows = {failure.row_index for failure in failures}
    keep = [index for index in range(frame.height) if index not in failed_rows]

    # The contribution: exactly the mapping's target columns, only fully-clean rows.
    contribution = frame.select(list(mapping.target_columns))[keep]

    if failures:
        log.warning(
            "mapping produced per-cell failures",
            extra={
                "mapping_rules_version": mapping.version,
                "rows_in": chunk.height,
                "rows_contributed": len(keep),
                "rows_failed": len(failed_rows),
                "cells_failed": len(failures),
                "columns_with_failures": sorted({failure.column for failure in failures}),
            },
        )

    return MappingResult(
        contribution=contribution,
        source_row_indices=tuple(keep),
        failures=tuple(failures),
    )
