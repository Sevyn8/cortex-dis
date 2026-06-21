"""dis-mapping — the pure four-sub-stage mapping engine (slice-05).

``apply_mapping(mapping, chunk)`` applies one source's mapping in the mandatory
``rename -> normalize -> cast -> derive`` order (D20) and returns a PARTIAL
canonical contribution: the source-owned, mapping-produced columns only. Identity
(``tenant_id``/``store_id``), ``trace_id``, and ``mapping_version_id`` are
consumer-injected after the engine runs (D8, hard rule 5); the engine never
populates them. Pure: no DB, GCS, Pub/Sub, network, or file I/O (D4).
"""

from __future__ import annotations

from dis_mapping.engine import apply_mapping
from dis_mapping.models import (
    DERIVE_GENERATOR_OPS,
    NORMALIZE_OPS,
    CastSpec,
    SourceMapping,
    TransformSpec,
)
from dis_mapping.result import CellNormalizationFailure, LogContext, MappingResult

__all__ = [
    "DERIVE_GENERATOR_OPS",
    "NORMALIZE_OPS",
    "CastSpec",
    "CellNormalizationFailure",
    "LogContext",
    "MappingResult",
    "SourceMapping",
    "TransformSpec",
    "apply_mapping",
]
