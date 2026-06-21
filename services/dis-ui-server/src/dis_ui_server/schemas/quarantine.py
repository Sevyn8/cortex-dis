"""Wire shapes + the single taxonomy crosswalk for the quarantine console (15a).

Two endpoints consume this: ``GET /quarantine`` (a list row + an open count) and
``GET /quarantine/{item_id}`` (one held item in full). Both are unioned across
``quarantined_rows`` and ``quarantined_chunks`` and tagged with ``kind``.

The load-bearing rule (slice principle "one canonical truth"): there is ONE
``failure_stage`` (DB enum) -> ``StageWire`` crosswalk here, and it drives BOTH the
displayed Stage AND the ``error_type`` filter. Display reads it forward (enum ->
bucket); the filter reads it in reverse (bucket -> the set of enum values for the
WHERE). Because both sides read the same dict, the four filter buttons stay
functional across both tables' stage vocabularies and the displayed Stage cannot
drift from the filter. Out-of-vocabulary stages fall into the OTHER bucket
explicitly (never silently dropped); a genuinely NEW DB stage is absent from the
map and fails loud (KeyError -> 500), the §2.6 no-silent-fallback posture.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Wire vocabularies (§2.6 - DB vocab never leaks; the UI sees these only).
Kind = Literal["row", "chunk"]
StatusWire = Literal["open", "resolved"]
WindowWire = Literal["24h", "7d", "30d"]
# The four screen buttons + the honest OTHER bucket for every other stage.
StageWire = Literal["source-shape", "canonical-shape", "fk", "normalization", "other"]

# THE crosswalk (DB failure_stage -> wire bucket). The four validation stages the
# screens expose map 1:1; every other stage (chunk-level and tail) buckets to
# "other" so nothing is dropped. Explicit, not ``.get`` - a new DB member fails loud.
_STAGE_DB_TO_WIRE: dict[str, StageWire] = {
    "PRE_MAPPING_VALIDATION": "source-shape",
    "POST_MAPPING_VALIDATION": "canonical-shape",
    "IDENTITY_VALIDATION": "fk",
    "MAPPING_EXECUTION": "normalization",
    "CANONICAL_WRITE": "other",
    "OTHER": "other",
    "PRE_INGEST_PII": "other",
    "BRONZE_WRITE": "other",
    "MAPPING_LOOKUP": "other",
}

# Status: NEW is open; the (currently unreachable, D82) terminal states are resolved.
_STATUS_DB_TO_WIRE: dict[str, StatusWire] = {
    "NEW": "open",
    "RESOLVED": "resolved",
    "DISMISSED": "resolved",
}


def stage_to_wire(failure_stage: str) -> StageWire:
    """Forward crosswalk for the displayed Stage. KeyError (500) on an unknown member."""
    return _STAGE_DB_TO_WIRE[failure_stage]


def stage_db_values_for(error_type: StageWire) -> list[str]:
    """Reverse crosswalk for the ``error_type`` filter: the DB enum values in this bucket."""
    return [db for db, wire in _STAGE_DB_TO_WIRE.items() if wire == error_type]


def status_to_wire(status: str) -> StatusWire:
    """Forward crosswalk for the displayed Status. KeyError (500) on an unknown member."""
    return _STATUS_DB_TO_WIRE[status]


def status_db_values_for(status: StatusWire) -> list[str]:
    """Reverse crosswalk for the Status filter: ``open`` -> ['NEW']; ``resolved`` -> terminal."""
    return [db for db, wire in _STATUS_DB_TO_WIRE.items() if wire == status]


class QuarantineListRow(BaseModel):
    """One held item in the console table (fields per slice 15a §a)."""

    id: str  # type-tagged held-item id "row:<uuid>"/"chunk:<uuid>" - opaque, round-tripped to detail
    kind: Kind
    trace_id: str  # Trace (copy affordance is the UI's; the server just returns it)
    source_id: str  # the filter key (Dashboard ?source= deep link)
    source: str  # display name; == source_id today (no source registry - fast-follow)
    error_reason: str  # Error: failure_reason (a FailureCode member)
    failure_stage: StageWire  # Stage: the single crosswalk
    failed_at: str  # Time: quarantined_at, ISO-8601
    status: StatusWire


class QuarantineListResponse(BaseModel):
    """The list body: the held items plus the header's open count.

    Not a success envelope (§2.4) - a purpose-built shape carrying the array AND the
    filter-INDEPENDENT open count the header badge shows ("3 open").
    """

    items: list[QuarantineListRow]
    open_count: int


class QuarantineDetail(BaseModel):
    """One held item in full, for the Row detail panel (fields per slice 15a §b)."""

    id: str  # the same type-tagged id
    kind: Kind
    trace_id: str
    source: str  # display fallback (source_id)
    failed_at: str  # quarantined_at, ISO-8601
    mapping_version: int | None  # the "v1" token; null for pre-lookup chunk failures
    error_reason: str  # failure_reason (FailureCode)
    failure_stage: StageWire
    error_context: str  # composed from failure_stage + failure_context (no second stored string)
    original_payload: dict[str, object] | None  # DEFERRED this slice -> always null (build-cost fast-follow)
    chain_depth: int  # literal 0 - no parent_trace_id lineage until Slice 12
