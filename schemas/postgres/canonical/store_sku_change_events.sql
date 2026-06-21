-- ============================================================================
-- DIS canonical schema: store_sku_change_events
--
-- Append-only event log of every non-sale change to a SKU's state at a store:
-- inventory movements, price changes, cost changes, regulatory changes, status
-- changes, and catalogue changes. Sales are NOT here; sales live in
-- canonical.store_sku_sale_events (different shape, different volume).
--
-- One row per change. Polymorphic structure: event_category +  event_subtype
-- classify the change; JSONB value_before / value_after carry the change in a
-- type-agnostic form; numeric_value_before / numeric_value_after / numeric_change
-- carry typed shortcuts for numeric attributes (prices, costs, stock).
--
-- Written exclusively by the streaming consumer, atomically with the upsert to
-- canonical.store_sku_current_position. Read by:
--   - Nightly Cloud SQL -> BigQuery export job (then partition dropped).
--   - Daily compute job (stock_age_days from latest INVENTORY RECEIPT;
--     unit_cost_trend_30day from recent COST events; etc.).
--   - Ops investigations within the retention window (~24-48h).
--
-- Every row carries mapping_version_id (architecture B1, v0.6).
--
-- ----------------------------------------------------------------------------
-- Partitioning: none for beta (D77 scope revised)
-- ----------------------------------------------------------------------------
-- This is a PLAIN table. It was PARTITION BY RANGE (event_date) with a fixed
-- bootstrap-created daily window, no DEFAULT partition, and no automation —
-- the same write-cliff shape Slice 30a removed from audit.events (D77), except
-- here the miss failed LOUD (batch nack), not silently. De-partitioned for
-- beta on the same disposable-rows/drop-recreate pattern (migration 0009).
--
-- Partitioning returns at Slice 21 (BQ archive + eviction), WITH automation
-- (decisions.md D29/D34). event_date stays NOT NULL + CHECK-consistent
-- (ck_ssce_event_date_matches_source_ts) so that re-partition is safe.
--
-- ----------------------------------------------------------------------------
-- Timestamps
-- ----------------------------------------------------------------------------
-- source_event_timestamp -- when the source recorded the change. Always
--                          populated; the analytics anchor for derived
--                          attribute compute and ops investigation.
-- effective_from         -- when the change takes effect. NULL means
--                          immediately (= source_event_timestamp). Populated
--                          only when source distinguishes (e.g., a price
--                          change recorded today, effective tomorrow).
-- effective_until        -- when the change ends. NULL means open-ended.
--                          Populated only for events with explicit end times
--                          (some price promotions with end dates).
--
-- ----------------------------------------------------------------------------
-- event_category vocabulary (closed; CHECK-enforced)
-- ----------------------------------------------------------------------------
--   INVENTORY  : stock movements
--   PRICE      : retail and promotional price changes
--   COST       : unit cost changes from supplier
--   REGULATORY : regulatory flag or type changes
--   STATUS     : SKU lifecycle status changes (ACTIVE / PAUSED / DELISTED)
--   CATALOGUE  : changes to catalogue context (name, category, packaging,
--                supplier, description, etc.)
--   OTHER      : catch-all for changes that don't fit; revisit if it grows.
--
-- ----------------------------------------------------------------------------
-- event_subtype vocabulary (open; TEXT, no DB CHECK)
-- ----------------------------------------------------------------------------
-- Documented and validated by Pandera in the streaming consumer. Common
-- values per category:
--   INVENTORY  : RECEIPT, ADJUSTMENT, COUNT, SALE_DECREMENT,
--                RETURN_INCREMENT, DAMAGE, RETURN_TO_SUPPLIER,
--                TRANSFER_IN, TRANSFER_OUT, SHRINKAGE
--   PRICE      : RETAIL_PRICE_CHANGE, PROMO_PRICE_START, PROMO_PRICE_END
--   COST       : COST_CHANGE
--   REGULATORY : FLAG_CHANGE, TYPE_CHANGE
--   STATUS     : STATUS_CHANGE
--   CATALOGUE  : NAME_CHANGE, CATEGORY_CHANGE, SUB_CATEGORY_CHANGE,
--                DEPARTMENT_CHANGE, PACKAGING_CHANGE, SUPPLIER_CHANGE,
--                DESCRIPTION_CHANGE
--   OTHER      : free-form
--
-- ----------------------------------------------------------------------------
-- Numeric shortcut columns
-- ----------------------------------------------------------------------------
-- numeric_value_before / numeric_value_after carry the change in NUMERIC form
-- for INVENTORY (stock_qty), PRICE (retail / promo price), and COST (unit
-- cost). Streaming consumer populates them alongside the JSONB value_before /
-- value_after; Pandera validates the contract. For REGULATORY, STATUS, and
-- CATALOGUE changes, these columns are typically NULL (change is non-numeric).
--
-- numeric_change is signed; populated for INVENTORY events to carry the
-- delta (positive = stock added, negative = stock removed).
--
-- ----------------------------------------------------------------------------
-- Phase 0 migration order
-- ----------------------------------------------------------------------------
--
-- 1. Schemas exist: canonical, identity_mirror, config.
-- 2. uuidv7() function installed.
-- 3. Target FK tables exist: identity_mirror.tenants,
--    identity_mirror.stores, config.source_mappings.
-- 4. Apply this DDL.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: canonical
--   - schema: identity_mirror, with tables tenants, stores
--   - schema: config, with table source_mappings
--   - function: uuidv7()
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Table (plain for beta; Slice 21 re-partitions by event_date)
-- ----------------------------------------------------------------------------

CREATE TABLE canonical.store_sku_change_events (

    -- ---------- Surrogate key ----------
    id                              UUID                            NOT NULL DEFAULT uuidv7(),

    -- ---------- Event date (Slice 21's re-partition key) ----------
    event_date                      DATE                            NOT NULL,
        -- Derived from source_event_timestamp::date at UTC. CHECK enforces.

    -- ---------- Identity ----------
    tenant_id                       UUID                            NOT NULL,
    store_id                        UUID                            NOT NULL,

    -- ---------- SKU identity ----------
    sku_id                          VARCHAR(128) COLLATE "C"        NOT NULL,
    sku_variant                     VARCHAR(128) COLLATE "C"        NULL,
    sku_lot_batch                   VARCHAR(128) COLLATE "C"        NULL,

    -- ---------- Cross-reference to current position ----------
    store_sku_current_position_id   UUID                            NULL,
        -- Soft cross-reference to canonical.store_sku_current_position.id at
        -- write time. Not a FK: lifecycle independence and bootstrap. NULL
        -- when not available.

    -- ---------- Event classification ----------
    event_category                  VARCHAR(32) COLLATE "C"         NOT NULL,
        -- INVENTORY, PRICE, COST, REGULATORY, STATUS, CATALOGUE, OTHER.
        -- Closed vocabulary, CHECK-enforced. Adding categories requires
        -- an Alembic migration to update the CHECK.
    event_subtype                   VARCHAR(64) COLLATE "C"         NOT NULL,
        -- Subtype within category (e.g., RECEIPT, RETAIL_PRICE_CHANGE).
        -- Open vocabulary; documented in table header; validated by Pandera.

    -- ---------- Timestamps ----------
    source_event_timestamp          TIMESTAMPTZ                     NOT NULL,
        -- When source recorded the change. Always populated. Indexed.
        -- The analytics anchor.
    effective_from                  TIMESTAMPTZ                     NULL,
        -- When the change takes effect. NULL = immediately (= source).
        -- Populated only when source distinguishes.
    effective_until                 TIMESTAMPTZ                     NULL,
        -- When the change ends. NULL = open-ended. Populated for events with
        -- explicit end times (some promotions).

    -- ---------- Change payload ----------
    attribute_name                  VARCHAR(64) COLLATE "C"         NULL,
        -- The canonical column that changed, when applicable (current_retail_price,
        -- unit_cost, stock_qty, regulatory_flag, sku_status, product_category,
        -- etc.). NULL for events that don't map to a single column.
    value_before                    JSONB                           NULL,
        -- Type-agnostic previous value. JSONB shape per event_category is
        -- documented in the table header and enforced by Pandera, not by DB.
    value_after                     JSONB                           NULL,
        -- Type-agnostic new value. Same shape rules as value_before.

    -- ---------- Numeric shortcut columns ----------
    numeric_value_before            NUMERIC(14, 4)                  NULL,
        -- Typed shortcut for numeric attributes (prices, costs, stock).
        -- NULL for non-numeric changes (status, regulatory flag, category, etc.).
    numeric_value_after             NUMERIC(14, 4)                  NULL,
        -- Typed shortcut for numeric attributes.
    numeric_change                  NUMERIC(14, 4)                  NULL,
        -- Signed delta. Populated for INVENTORY events
        -- (numeric_value_after - numeric_value_before). Positive = stock added;
        -- negative = stock removed. NULL for non-numeric changes.

    -- ---------- Reason ----------
    reason_code                     VARCHAR(64) COLLATE "C"         NULL,
        -- Source's reason code (e.g., PO_RECEIPT, CYCLE_COUNT, DAMAGE_OUT).
    reason_note                     VARCHAR(256)                    NULL,
        -- Free-text reason or note from source.

    -- ---------- Category-specific context ----------
    change_context                  JSONB                           NULL,
        -- Event-category-specific structured context:
        --   INVENTORY: po_id, transfer_id, related_sale_event_id, etc.
        --   PRICE: pricing_zone, promo_identifier, etc.
        --   COST: supplier_id, po_id, etc.
        -- Shape per category documented in streaming consumer; not DB-enforced.

    -- ---------- Source event identity (D33 dedup key; D38 resolution) ----------
    source_id                       VARCHAR(128) COLLATE "C"        NOT NULL,
        -- Source registration identifier of the originating source. Matches
        -- config.source_mappings.source_id and bronze.data_ingress_events
        -- .source_id (varchar(128) COLLATE "C"). Component of the D33
        -- read-time dedup key (tenant_id, store_id, source_id,
        -- source_event_id). Consumer-injected from the ingress.ready
        -- envelope, cross-checked against the GCS path and the bronze row.
    source_event_id                 VARCHAR(256) COLLATE "C"        NOT NULL,
        -- Per-source event identifier completing the D33 dedup key. Change
        -- events carry no native source event-id column, so the deterministic
        -- fallback bronze_ref || ':' || chunk_row_index applies
        -- (redelivery-stable, NOT correction-collapsing; D65).
        -- Consumer-injected.

    -- ---------- DIS metadata (load-bearing) ----------
    mapping_version_id              BIGINT                          NOT NULL,
    trace_id                        UUID                            NOT NULL,
    dis_channel                     VARCHAR(32) COLLATE "C"         NOT NULL,
    last_updated_at                 TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),

    -- ---------- DIS metadata (diagnostic, lineage) ----------
    ingest_metadata                 JSONB                           NULL,
        -- JSONB: source_name, source_event_timestamp,
        -- dis_received_timestamp, dis_published_timestamp, csv_row_num.
        -- (source_event_id moved to a first-class column, D38/0003.)

    -- ---------- Primary key ----------
    CONSTRAINT pk_ssce
        PRIMARY KEY (id),
        -- (id, event_date) while partitioned — the composite existed only to
        -- satisfy the partition-key-in-PK requirement (the D77 PK precedent).

    -- ---------- Foreign keys ----------
    CONSTRAINT fk_ssce_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_ssce_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT fk_ssce_mapping_version
        FOREIGN KEY (mapping_version_id)
        REFERENCES config.source_mappings (mapping_version_id),

    -- ---------- Check constraints ----------
    CONSTRAINT ck_ssce_event_category_vocab
        CHECK (event_category IN (
            'INVENTORY', 'PRICE', 'COST', 'REGULATORY',
            'STATUS', 'CATALOGUE', 'OTHER'
        )),

    CONSTRAINT ck_ssce_event_date_matches_source_ts
        CHECK (event_date = (source_event_timestamp AT TIME ZONE 'UTC')::date),

    CONSTRAINT ck_ssce_effective_until_after_from
        CHECK (
            effective_until IS NULL
            OR effective_from IS NULL
            OR effective_until > effective_from
        ),

    CONSTRAINT ck_ssce_at_least_one_value_present
        CHECK (value_before IS NOT NULL OR value_after IS NOT NULL)

);


-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------

-- Primary daily-compute access pattern: latest event of a category for a SKU,
-- or all events of a category for a SKU over a time window.
-- Stock-age compute, cost-trend compute, regulatory-change history, etc.
CREATE INDEX ix_ssce_tenant_store_sku_category_time
    ON canonical.store_sku_change_events
    (tenant_id, store_id, sku_id, sku_variant, sku_lot_batch,
     event_category, source_event_timestamp DESC);

-- Store-level analytics: all changes of a category at a store on a date.
CREATE INDEX ix_ssce_tenant_store_category_time
    ON canonical.store_sku_change_events
    (tenant_id, store_id, event_category, source_event_timestamp);

-- Cross-tenant time-range queries (ops, BQ export filtering).
CREATE INDEX ix_ssce_source_event_timestamp
    ON canonical.store_sku_change_events (source_event_timestamp);

-- D33 read-time latest-wins dedup window (D38/0003): partition prefix +
-- event-time ordering for ROW_NUMBER() OVER (PARTITION BY tenant_id,
-- store_id, source_id, source_event_id ORDER BY source_event_timestamp DESC, ...).
CREATE INDEX ix_ssce_dedup_key
    ON canonical.store_sku_change_events
    (tenant_id, store_id, source_id, source_event_id, source_event_timestamp DESC);

-- Navigate from current_position row to its change history.
CREATE INDEX ix_ssce_current_position
    ON canonical.store_sku_change_events (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;

-- Audit lookups.
CREATE INDEX ix_ssce_trace_id
    ON canonical.store_sku_change_events (trace_id);

-- B1 dispute and replay investigations.
CREATE INDEX ix_ssce_mapping_version
    ON canonical.store_sku_change_events (mapping_version_id);


-- ----------------------------------------------------------------------------
-- Trigger: keep last_updated_at fresh on UPDATE
--
-- Change events are conceptually append-only; updates are rare (out-of-band
-- corrections, replay-with-fix). Trigger kept for consistency with other
-- canonical event tables and to keep the column honest if updates happen.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION canonical.set_ssce_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ssce_set_last_updated_at
    BEFORE UPDATE ON canonical.store_sku_change_events
    FOR EACH ROW
    EXECUTE FUNCTION canonical.set_ssce_last_updated_at();


-- ----------------------------------------------------------------------------
-- Row-Level Security
-- ----------------------------------------------------------------------------

ALTER TABLE canonical.store_sku_change_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical.store_sku_change_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON canonical.store_sku_change_events
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        OR current_setting('app.user_type', true) = 'PLATFORM'
    )
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);


-- ----------------------------------------------------------------------------
-- Comments
-- ----------------------------------------------------------------------------

COMMENT ON TABLE canonical.store_sku_change_events IS
'Append-only event log of every non-sale change to a SKU''s state at a store: inventory, price, cost, regulatory, status, catalogue. Plain (non-partitioned) for beta; Slice 21 re-partitions by event_date for BQ archive + eviction. Written atomically with store_sku_current_position upserts by the streaming consumer. Tenant-isolated via RLS. Polymorphic structure: event_category + event_subtype classify the change; JSONB and numeric-shortcut columns carry the change payload.';

COMMENT ON COLUMN canonical.store_sku_change_events.id IS
'Surrogate identifier. UUIDv7. Primary key.';

COMMENT ON COLUMN canonical.store_sku_change_events.event_date IS
'DATE derived from source_event_timestamp::date at UTC. CHECK constraint enforces the derivation. Slice 21''s re-partition key.';

COMMENT ON COLUMN canonical.store_sku_change_events.tenant_id IS
'Tenant owning this row. FK to identity_mirror.tenants. RLS scopes every read and write.';

COMMENT ON COLUMN canonical.store_sku_change_events.store_id IS
'Store where the change occurred. FK to identity_mirror.stores.';

COMMENT ON COLUMN canonical.store_sku_change_events.store_sku_current_position_id IS
'Soft cross-reference to canonical.store_sku_current_position.id at write time. Not a FK: lifecycle independence and bootstrap. NULL when not available.';

COMMENT ON COLUMN canonical.store_sku_change_events.event_category IS
'Classification of the change. Closed vocabulary, CHECK-enforced: INVENTORY, PRICE, COST, REGULATORY, STATUS, CATALOGUE, OTHER. Adding categories requires an Alembic migration.';

COMMENT ON COLUMN canonical.store_sku_change_events.event_subtype IS
'Subtype within event_category. Open vocabulary; documented in table header; validated by Pandera in the streaming consumer.';

COMMENT ON COLUMN canonical.store_sku_change_events.source_event_timestamp IS
'When the source recorded the change, normalized to UTC at ingest. Always populated; the analytics anchor for derived-attribute compute and ops investigation.';

COMMENT ON COLUMN canonical.store_sku_change_events.effective_from IS
'When the change takes effect for downstream consumers. NULL means immediately (= source_event_timestamp). Populated only when source distinguishes (e.g., a price change recorded today, effective tomorrow).';

COMMENT ON COLUMN canonical.store_sku_change_events.effective_until IS
'When the change ends. NULL means open-ended. Populated only for events with explicit end times (some price promotions).';

COMMENT ON COLUMN canonical.store_sku_change_events.attribute_name IS
'The canonical column that changed (current_retail_price, unit_cost, stock_qty, regulatory_flag, sku_status, product_category, etc.). NULL for events that don''t map to a single column.';

COMMENT ON COLUMN canonical.store_sku_change_events.value_before IS
'Type-agnostic previous value. JSONB. Shape per event_category documented in table header; enforced by Pandera, not by DB.';

COMMENT ON COLUMN canonical.store_sku_change_events.value_after IS
'Type-agnostic new value. JSONB. Shape per event_category documented in table header; enforced by Pandera, not by DB.';

COMMENT ON COLUMN canonical.store_sku_change_events.numeric_value_before IS
'Typed shortcut for numeric attributes (prices, costs, stock). Populated by the streaming consumer for INVENTORY, PRICE, and COST changes alongside JSONB value_before. NULL for non-numeric changes (status, regulatory flag, category, etc.).';

COMMENT ON COLUMN canonical.store_sku_change_events.numeric_value_after IS
'Typed shortcut for numeric attributes. Same population rules as numeric_value_before.';

COMMENT ON COLUMN canonical.store_sku_change_events.numeric_change IS
'Signed delta. Populated for INVENTORY events: positive = stock added, negative = stock removed. NULL for non-numeric changes and for PRICE/COST changes where before/after values are the analytics surface.';

COMMENT ON COLUMN canonical.store_sku_change_events.reason_code IS
'Source''s reason code for the change (e.g., PO_RECEIPT, CYCLE_COUNT, DAMAGE_OUT). Free-text.';

COMMENT ON COLUMN canonical.store_sku_change_events.reason_note IS
'Free-text reason or note from source. Up to 256 chars.';

COMMENT ON COLUMN canonical.store_sku_change_events.change_context IS
'Event-category-specific structured context. JSONB. Shape per category documented in streaming consumer; not DB-enforced. Examples: po_id and transfer_id for INVENTORY; pricing_zone for PRICE; supplier_id for COST.';

COMMENT ON COLUMN canonical.store_sku_change_events.mapping_version_id IS
'Mapping version that produced this row (architecture B1, v0.6). FK to config.source_mappings. Replay defaults to the version recorded here.';

COMMENT ON COLUMN canonical.store_sku_change_events.trace_id IS
'End-to-end trace identifier for the chunk that produced this row. Joins to BigQuery audit_events.';

COMMENT ON COLUMN canonical.store_sku_change_events.dis_channel IS
'Ingress channel that delivered the data (csv_upload, api, csv_erp, reverse_api).';

COMMENT ON COLUMN canonical.store_sku_change_events.last_updated_at IS
'When this row was last touched in DIS Postgres. DB-generated. Change events are conceptually append-only; trigger refreshes this on any UPDATE if one happens.';

COMMENT ON COLUMN canonical.store_sku_change_events.ingest_metadata IS
'JSONB diagnostic and lineage detail: source_name, source_event_timestamp, dis_received_timestamp, dis_published_timestamp, csv_row_num. Designed to evolve. (source_event_id moved to a first-class column, D38/0003.)';

COMMENT ON COLUMN canonical.store_sku_change_events.source_id IS
'Source registration identifier of the originating source. Matches config.source_mappings.source_id and bronze.data_ingress_events.source_id (varchar(128) COLLATE C, introspected). Component of the D33 read-time dedup key (tenant_id, store_id, source_id, source_event_id). Consumer-injected from the ingress.ready envelope, cross-checked against the GCS path and the bronze row (D38 resolution).';

COMMENT ON COLUMN canonical.store_sku_change_events.source_event_id IS
'Per-source event identifier completing the D33 dedup key. Change events carry no native source event-id column, so the deterministic fallback bronze_ref || '':'' || chunk_row_index applies (redelivery-stable, NOT correction-collapsing; D65). Consumer-injected (D38 resolution).';
