"""Engine result types — the partial-contribution surface (slice-05 OQ1).

``MappingResult.contribution`` carries the source-owned, mapping-produced columns
ONLY: never ``tenant_id`` / ``store_id`` / ``trace_id`` / ``mapping_version_id``
(consumer-injected after the engine runs; D8, hard rule 5). A row with ANY failed
cell yields no contribution — whole-row drop, no nulled-cell pass-through.

Per-cell failures are DATA, not exceptions: the engine reports them alongside the
rows that succeeded, applies no pass-threshold, and routes nothing (B2 is the
consumer's, Slice 10). ``row_index`` is carried so the consumer can later route at
either chunk or row grain without an engine change.

The failure ``value`` is the cell as it entered the failing transform — it is part
of the D20-mandated quarantine payload ("column X, value Y, expected format Z").
It is NEVER logged by this lib (root CLAUDE.md: never log PII or raw payloads);
log lines carry column/op names and counts only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import polars as pl

from dis_core.logging import LogContext

__all__ = ["CellNormalizationFailure", "LogContext", "MappingResult"]


@dataclass(frozen=True)
class CellNormalizationFailure:
    """One cell that could not be normalized / cast / derived.

    ``transform_index`` is the failing op's position in the column's declared
    transform list (0-based), so a multi-transform failure is attributable to the
    exact step that failed; remaining steps were skipped for that cell. For the
    cast sub-stage (a single op per column) it is always 0 with ``op="cast"``.
    """

    row_index: int
    column: str
    source_column: str | None
    value: str | None
    op: str
    transform_index: int
    expected_format: str
    stage: Literal["normalize", "cast", "derive"]
    reason: str


@dataclass(frozen=True)
class MappingResult:
    """The engine's output: a partial canonical contribution plus per-cell failures.

    ``source_row_indices`` is parallel to ``contribution`` rows (positions in the
    input chunk), so the consumer can join contributions and failures back to
    bronze rows without the engine smuggling a non-canonical column into the
    contribution.
    """

    contribution: pl.DataFrame
    source_row_indices: tuple[int, ...]
    failures: tuple[CellNormalizationFailure, ...] = field(default=())

    @property
    def failed_row_indices(self) -> tuple[int, ...]:
        """Distinct input-chunk row positions that produced no contribution."""
        return tuple(sorted({f.row_index for f in self.failures}))
