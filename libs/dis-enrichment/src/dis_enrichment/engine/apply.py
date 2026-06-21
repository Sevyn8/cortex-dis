"""``apply_enrichment`` — the pure lookup-enrichment step (slice-5b; D94, D95).

A pure function over ``(contribution, facts, table)``: no Postgres, GCS, Pub/Sub,
network, or file I/O. It overwrites each registered field for ``table`` with the
handed-in internal-source value (broadcast across every row), so the lib's value
WINS over any mapping-produced value of the same name (D95). Column-wise mutation
only: row count and row order are preserved (the D94 row-alignment contract — the
consumer reuses its ``MappingResult.source_row_indices`` unchanged).

The lib reads nothing: the consumer resolves the values from the authoritative
internal source and hands them in (the pure-lib / consumer-does-I/O split that
mirrors dis-mapping). Log lines carry column names and counts — never a value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from dis_core.errors import EnrichmentError
from dis_core.logging import LogContext, get_logger
from dis_enrichment.registry import enrichment_fields
from dis_enrichment.result import EnrichmentResult


def apply_enrichment(
    contribution: pl.DataFrame,
    facts: Mapping[str, Any],
    *,
    table: str,
    log_context: LogContext | None = None,
) -> EnrichmentResult:
    """Overwrite ``table``'s registered fields in ``contribution`` from ``facts``.

    ``facts`` maps canonical field -> resolved internal-source value. A registered
    field MISSING from ``facts`` is a consumer-contract violation (the consumer
    must resolve every registered field) and raises ``EnrichmentError``. A
    registered field PRESENT-BUT-BLANK (``None`` / empty) is written through
    as-handed-in this slice; the loud-fail-on-blank guard is D97 (deferred, not
    built) — today the source fields are NOT NULL so this path cannot bite, but it
    is now defined rather than undefined.

    ``log_context`` binds log fields only; it never enters the frame.
    """
    log = get_logger(
        "dis-enrichment",
        stage="enrichment",
        tenant_id=log_context.tenant_id if log_context else None,
        trace_id=log_context.trace_id if log_context else None,
    )

    fields = enrichment_fields(table)
    missing = [name for name in fields if name not in facts]
    if missing:
        raise EnrichmentError(
            f"enrichment facts for table {table!r} are missing registered field(s) "
            f"{sorted(missing)}; the consumer must resolve every registered field from "
            "the authoritative internal source before calling the engine (code-quality rule 4)",
            table=table,
            tenant_id=log_context.tenant_id if log_context else None,
            trace_id=log_context.trace_id if log_context else None,
        )

    if not fields:
        # No registered field for this table (e.g. the event path): the seam may sit
        # upstream of the branch, but enrichment is a no-op here. Output unchanged.
        return EnrichmentResult(contribution=contribution, enriched_columns=())

    # Output-wins (D95): ``with_columns`` REPLACES a same-named column, so a
    # mapping-produced value of a registered field cannot survive — the lib's value
    # wins by construction; a not-yet-present field (e.g. tax_treatment) is created.
    enriched = contribution.with_columns([pl.lit(facts[name]).alias(name) for name in fields])
    log.info(
        "enrichment applied",
        extra={"table": table, "rows": contribution.height, "fields": list(fields)},
    )
    return EnrichmentResult(contribution=enriched, enriched_columns=tuple(fields))
