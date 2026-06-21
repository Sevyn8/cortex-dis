"""``canonical.store_sku_signal_history`` — daily computed signals (append-only).

Introspected facts:
- PK ``(id)``; plain for beta (migration 0009, D77 scope revised — Slice 21
  re-partitions by ``as_of_date``); natural key
  ``uq_sssh_natural`` NULLS NOT DISTINCT on
  ``(tenant_id, store_id, sku_id, sku_variant, sku_lot_batch, as_of_date)``.
- FKs: ``(tenant_id) -> tenants``; ``(tenant_id, store_id) -> stores``.
- **No ``mapping_version_id``** — this is daily-compute output, not mapping-produced
  (decisions.md D22 / D31 / D32, CLAUDE.md hard rule 5). Provenance is ``trace_id``
  plus ``compute_metadata``. There is NO FK to config.source_mappings here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from dis_canonical.shared import (
    CanonicalModel,
    Numeric10_4,
    Numeric12_4,
    StoreId,
    Str128,
    TenantId,
    TraceId,
)


class StoreSkuSignalHistory(CanonicalModel):
    id: UUID | None = None  # uuid NOT NULL DEFAULT uuidv7()
    as_of_date: date  # date NOT NULL (partition key)
    tenant_id: TenantId  # uuid NOT NULL
    store_id: StoreId  # uuid NOT NULL (composite FK)

    sku_id: Str128  # NOT NULL
    sku_variant: Str128 | None = None
    sku_lot_batch: Str128 | None = None
    store_sku_current_position_id: UUID | None = None

    velocity_7day: Numeric10_4 | None = None  # numeric(10,4)
    stock_age_days: int | None = None  # smallint
    unit_cost_trend_30day: Numeric12_4 | None = None  # numeric(12,4)

    # Provenance — note: NO mapping_version_id (daily-compute output, not mapping)
    trace_id: TraceId  # uuid NOT NULL
    created_at: datetime | None = None  # timestamptz NOT NULL DEFAULT now()
    compute_metadata: dict[str, Any] | None = None  # jsonb
