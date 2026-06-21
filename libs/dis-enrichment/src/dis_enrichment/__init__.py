"""dis-enrichment — pure lookup-enrichment over a canonical contribution (slice-5b).

``apply_enrichment(contribution, facts, table=...)`` overwrites the registered
canonical fields for ``table`` with the authoritative internal-source values the
consumer hands in — the lib's value WINS over the mapping (D95). It resolves each
field from an authoritative internal source NAMED in the registry, but reads
NOTHING itself: the consumer does the I/O and hands in the already-read facts (the
pure-lib / consumer-does-I/O split that mirrors dis-mapping). Enrichment runs
before post-validation, so its values pass the same canonical-shape gate (D94).

Lookup only — never computed/derived attributes (velocity, stock age, cost trend);
those are the daily-compute service, out of scope for this lib forever.
"""

from __future__ import annotations

from dis_enrichment.engine import apply_enrichment
from dis_enrichment.registry import (
    CURRENT_POSITION,
    ENRICHMENT_REGISTRY,
    EnrichmentField,
    enrichment_fields,
)
from dis_enrichment.result import EnrichmentResult, LogContext

__all__ = [
    "CURRENT_POSITION",
    "ENRICHMENT_REGISTRY",
    "EnrichmentField",
    "EnrichmentResult",
    "LogContext",
    "apply_enrichment",
    "enrichment_fields",
]
