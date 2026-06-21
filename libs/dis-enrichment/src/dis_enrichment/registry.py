"""The enrichment registry — a code-owned declaration behind a swappable seam (D95).

Each ``EnrichmentField`` names: the canonical field the lib writes, the
authoritative internal source and the field on it the value resolves from, and a
per-field table-scope flag (which canonical tables the field enriches). The source
is RECORDED here but the lib NEVER reads it — the consumer does the I/O and hands
in the resolved value (the pure-lib contract).

Source-agnostic in the lib's identity: ``identity_mirror.stores`` is the first
source wired, not the definition; other internal sources register later without
changing the resolution logic. ``enrichment_fields`` is the seam a config-table
loader can replace later.

Only the current-position table is wired this slice; the ``tables`` flag exists in
the shape so the event/history tables register later without a redesign.
"""

from __future__ import annotations

from dataclasses import dataclass

# Table-scope tokens. Lib-owned plain strings (NOT dis_canonical model imports) so
# the lib stays pure and table/source-agnostic; the consumer maps its
# ``target_model is StoreSkuCurrentPosition`` onto CURRENT_POSITION (Gap #2,
# resolved at scaffold: one token, no dis_canonical dependency in the pure lib).
CURRENT_POSITION = "store_sku_current_position"

__all__ = ["CURRENT_POSITION", "ENRICHMENT_REGISTRY", "EnrichmentField", "enrichment_fields"]


@dataclass(frozen=True)
class EnrichmentField:
    """One registered canonical field and how it resolves (mechanism, not policy)."""

    canonical_field: str
    source: str
    source_field: str
    tables: frozenset[str]


ENRICHMENT_REGISTRY: tuple[EnrichmentField, ...] = (
    EnrichmentField(
        canonical_field="currency",
        source="identity_mirror.stores",
        source_field="currency",
        tables=frozenset({CURRENT_POSITION}),
    ),
    EnrichmentField(
        canonical_field="tax_treatment",
        source="identity_mirror.stores",
        source_field="tax_treatment",
        tables=frozenset({CURRENT_POSITION}),
    ),
)


def enrichment_fields(table: str) -> tuple[str, ...]:
    """The canonical fields the lib enriches for ``table`` (registry-derived seam).

    Returns an empty tuple for a table with no registered fields; this is how the
    engine no-ops on the event path even though the seam sits upstream.
    """
    return tuple(f.canonical_field for f in ENRICHMENT_REGISTRY if table in f.tables)
