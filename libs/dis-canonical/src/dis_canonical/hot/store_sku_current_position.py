"""``canonical.store_sku_current_position`` — the hot table (one row per SKU instance).

Field shapes derived by introspecting the live ithina_dis_db schema (plan mode).
Key load-bearing facts:
- PK ``(id)``; natural key ``uq_sscp_natural_key``: a unique COALESCE-sentinel
  expression index on ``(tenant_id, store_id, sku_id, COALESCE(sku_variant,''),
  COALESCE(sku_lot_batch,''))`` — the ON CONFLICT arbiter (M-HOTKEY/0004; ''
  is engine-impossible via the sentinel CHECKs). Not partitioned.
- FKs: ``(tenant_id) -> identity_mirror.tenants``; composite
  ``(tenant_id, store_id) -> identity_mirror.stores`` (post-D36 store keying);
  ``(mapping_version_id) -> config.source_mappings``.
- ``mapping_version_id bigint NOT NULL`` (D22), ``trace_id uuid NOT NULL``.
- Enums: ``tax_treatment`` NOT NULL; ``expiry_source`` nullable.

DB-generated columns (``id`` default ``uuidv7()``; ``last_updated_at`` default
``now()``; ``regulatory_flag`` default ``false``) are Optional here: correct
pre-insert, and acceptable for readers. See libs/dis-canonical/CLAUDE.md for the
recorded read/write tension.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from dis_canonical.shared import (
    CanonicalModel,
    CurrencyCode,
    ExpirySource,
    MappingVersionId,
    Numeric3_2,
    Numeric8_3,
    Numeric10_4,
    Numeric12_4,
    Numeric14_3,
    StoreId,
    Str32,
    Str64,
    Str128,
    TaxTreatment,
    TenantId,
    TraceId,
)


class StoreSkuCurrentPosition(CanonicalModel):
    # Identity / DB-generated
    id: UUID | None = None  # uuid NOT NULL DEFAULT uuidv7()
    tenant_id: TenantId  # uuid NOT NULL (FK tenants)
    store_id: StoreId  # uuid NOT NULL (composite FK stores)

    # Natural key
    sku_id: Str128  # varchar(128) NOT NULL
    sku_variant: Str128 | None = None  # varchar(128) NULL
    sku_lot_batch: Str128 | None = None  # varchar(128) NULL

    # Catalogue context
    barcode: Str128 | None = None
    product_name: Str128  # NOT NULL
    product_description: Str128 | None = None
    product_category: Str128 | None = None  # varchar(128) NULL (16j; was NOT NULL)
    product_sub_category: Str128 | None = None
    product_department: Str128 | None = None
    supplier_id: Str128 | None = None
    packaging_type: Str128 | None = None
    sku_size: Numeric8_3 | None = None  # numeric(8,3)
    unit_of_measure: Str64 | None = None

    # Pricing / cost
    current_retail_price: Numeric12_4  # numeric(12,4) NOT NULL
    unit_cost: Numeric12_4 | None = None  # numeric(12,4) NULL (16j; was NOT NULL)
    promo_price: Numeric12_4 | None = None
    promo_identifier: Str128 | None = None
    yesterday_retail_price: Numeric12_4 | None = None
    tax_treatment: TaxTreatment  # tax_treatment_enum NOT NULL

    # Inventory / lifecycle
    stock_qty: Numeric14_3 | None = None
    lead_time_days: int | None = None  # smallint
    expiry_date: date | None = None
    receipt_date: date | None = None
    expiry_source: ExpirySource | None = None  # expiry_source_enum NULL
    expiry_confidence: Numeric3_2 | None = None  # numeric(3,2)
    regulatory_flag: bool | None = None  # boolean NULL DEFAULT false
    regulatory_type: Str128 | None = None
    currency: CurrencyCode  # char(3) NOT NULL
    reorder_point: Numeric14_3 | None = None
    sku_status: Str32 | None = None

    # Daily-computed derived signals (refreshed by daily-compute; D31)
    velocity_7day: Numeric10_4 | None = None  # numeric(10,4)
    stock_age_days: int | None = None  # smallint
    unit_cost_trend_30day: Numeric12_4 | None = None
    attribute_staleness_map: dict[str, Any] | None = None  # jsonb

    # Event-time-wins reference (D64, migration 0003)
    last_source_event_at: datetime | None = None  # timestamptz NULL: NULL = never event-written

    # Provenance
    mapping_version_id: MappingVersionId  # bigint NOT NULL (D22)
    trace_id: TraceId  # uuid NOT NULL
    dis_channel: Str32  # varchar(32) NOT NULL
    last_updated_at: datetime | None = None  # timestamptz NOT NULL DEFAULT now()
    ingest_metadata: dict[str, Any] | None = None  # jsonb
