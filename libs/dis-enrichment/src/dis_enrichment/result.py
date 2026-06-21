"""Engine result type — the enrichment surface (mirrors dis-mapping's MappingResult).

``EnrichmentResult.contribution`` is the input contribution with the registered
fields overwritten by the handed-in internal-source values; it has the SAME row
count and SAME row order as the input (column-wise mutation only, never row
filtering). The consumer reuses its ``MappingResult.source_row_indices`` unchanged
across enrichment — that is the D94 row-alignment contract.

``enriched_columns`` names exactly the columns the lib set or overwrote (the
output-wins audit trail; empty when the target table has no registered fields).
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from dis_core.logging import LogContext

__all__ = ["EnrichmentResult", "LogContext"]


@dataclass(frozen=True)
class EnrichmentResult:
    """The enrichment output: the column-enriched contribution + the columns touched."""

    contribution: pl.DataFrame
    enriched_columns: tuple[str, ...]
