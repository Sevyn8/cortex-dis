"""``canonical.store_sku_sale_events`` — sale line-items (SALE, RETURN, VOID).

Introspected facts:
- PK ``(id)``; plain for beta (migration 0009, D77 scope revised — Slice 21
  re-partitions by ``event_date``); **no** UNIQUE
  (strictly append-only, D33 — corrections are separate rows, latest-wins at read).
- FKs: ``(tenant_id) -> tenants``; ``(tenant_id, store_id) -> stores``;
  ``(mapping_version_id) -> source_mappings``.
- ``event_subtype`` CHECK vocab {SALE, RETURN, VOID}; ``tax_treatment`` NOT NULL.
- ``event_date date NOT NULL`` (CHECK: equals source_sale_timestamp at UTC date).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from dis_canonical.shared import (
    CanonicalModel,
    CurrencyCode,
    MappingVersionId,
    Numeric5_2,
    Numeric12_4,
    Numeric14_3,
    SaleEventSubtype,
    StoreId,
    Str32,
    Str64,
    Str128,
    Str256,
    TaxTreatment,
    TenantId,
    TraceId,
)


class StoreSkuSaleEvent(CanonicalModel):
    id: UUID | None = None  # uuid NOT NULL DEFAULT uuidv7()
    event_date: date  # date NOT NULL (partition key)
    tenant_id: TenantId  # uuid NOT NULL
    store_id: StoreId  # uuid NOT NULL (composite FK)

    sku_id: Str128  # NOT NULL
    sku_variant: Str128 | None = None
    sku_lot_batch: Str128 | None = None

    event_subtype: SaleEventSubtype  # varchar(32) NOT NULL, CHECK vocab
    source_sale_timestamp: datetime  # timestamptz NOT NULL
    transaction_id: Str128 | None = None
    line_item_seq: int | None = None  # smallint

    quantity: Numeric14_3  # numeric(14,3) NOT NULL
    unit_retail_price: Numeric12_4  # NOT NULL
    unit_sale_price: Numeric12_4  # NOT NULL
    discount_amount: Numeric12_4 | None = None
    discount_pct: Numeric5_2 | None = None  # numeric(5,2)
    unit_cost: Numeric12_4 | None = None
    promo_identifier: Str128 | None = None
    tax_amount: Numeric12_4 | None = None
    tax_treatment: TaxTreatment  # NOT NULL
    currency: CurrencyCode  # char(3) NOT NULL
    payment_method: Str64 | None = None
    customer_token: Str128 | None = None  # tokenized PII (dis-pii), never raw
    sale_channel: Str32 | None = None

    store_sku_current_position_id: UUID | None = None
    related_sale_event_id: UUID | None = None

    # Source event identity (D33 dedup key; D38 resolution, migration 0003)
    source_id: Str128  # varchar(128) COLLATE "C" NOT NULL (matches config.source_mappings.source_id)
    source_event_id: Str256  # varchar(256) COLLATE "C" NOT NULL (txn_id:line_item_seq or D65 fallback)

    # Provenance
    mapping_version_id: MappingVersionId  # bigint NOT NULL (D22)
    trace_id: TraceId  # uuid NOT NULL
    dis_channel: Str32  # NOT NULL
    last_updated_at: datetime | None = None  # timestamptz NOT NULL DEFAULT now()
    ingest_metadata: dict[str, Any] | None = None  # jsonb
