"""``canonical.store_sku_change_events`` — non-sale state changes (polymorphic).

Introspected facts:
- PK ``(id)``; plain for beta (migration 0009, D77 scope revised — Slice 21
  re-partitions by ``event_date``); **no** UNIQUE (append-only, D33).
- FKs: ``(tenant_id) -> tenants``; ``(tenant_id, store_id) -> stores``;
  ``(mapping_version_id) -> source_mappings``.
- ``event_category`` CHECK vocab {INVENTORY, PRICE, COST, REGULATORY, STATUS,
  CATALOGUE, OTHER}; ``event_subtype varchar(64) NOT NULL`` (free-form, no CHECK).
- Polymorphic payload via ``value_before``/``value_after`` jsonb plus typed numeric
  shortcut columns ``numeric_value_*`` (numeric(14,4)).
- ``event_date date NOT NULL`` (CHECK: equals source_event_timestamp at UTC date).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from dis_canonical.shared import (
    CanonicalModel,
    EventCategory,
    MappingVersionId,
    Numeric14_4,
    StoreId,
    Str32,
    Str64,
    Str128,
    Str256,
    TenantId,
    TraceId,
)


class StoreSkuChangeEvent(CanonicalModel):
    id: UUID | None = None  # uuid NOT NULL DEFAULT uuidv7()
    event_date: date  # date NOT NULL (partition key)
    tenant_id: TenantId  # uuid NOT NULL
    store_id: StoreId  # uuid NOT NULL (composite FK)

    sku_id: Str128  # NOT NULL
    sku_variant: Str128 | None = None
    sku_lot_batch: Str128 | None = None
    store_sku_current_position_id: UUID | None = None

    event_category: EventCategory  # varchar(32) NOT NULL, CHECK vocab
    event_subtype: Str64  # varchar(64) NOT NULL (no CHECK vocab)
    source_event_timestamp: datetime  # timestamptz NOT NULL
    effective_from: datetime | None = None
    effective_until: datetime | None = None

    attribute_name: Str64 | None = None
    value_before: Any | None = None  # jsonb (CHECK: value_before OR value_after present)
    value_after: Any | None = None  # jsonb
    numeric_value_before: Numeric14_4 | None = None  # numeric(14,4)
    numeric_value_after: Numeric14_4 | None = None
    numeric_change: Numeric14_4 | None = None
    reason_code: Str64 | None = None
    reason_note: Str256 | None = None
    change_context: dict[str, Any] | None = None  # jsonb

    # Source event identity (D33 dedup key; D38 resolution, migration 0003)
    source_id: Str128  # varchar(128) COLLATE "C" NOT NULL (matches config.source_mappings.source_id)
    source_event_id: Str256  # varchar(256) COLLATE "C" NOT NULL (no native id on change events: D65 fallback)

    # Provenance
    mapping_version_id: MappingVersionId  # bigint NOT NULL (D22)
    trace_id: TraceId  # uuid NOT NULL
    dis_channel: Str32  # NOT NULL
    last_updated_at: datetime | None = None  # timestamptz NOT NULL DEFAULT now()
    ingest_metadata: dict[str, Any] | None = None  # jsonb
