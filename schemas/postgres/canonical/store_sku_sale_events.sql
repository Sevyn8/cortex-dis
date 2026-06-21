-- ============================================================================
-- DIS canonical schema: store_sku_sale_events
--
-- Append-only event log: every sale line-item, return, and void at every store
-- for every SKU instance. The highest-volume table in canonical. Daily volume
-- depends on tenant count and store throughput; v1.0 target is ~100M rows/day.
-- Written exclusively by the streaming consumer, atomically with the upsert to
-- canonical.store_sku_current_position. Read by:
--   - Nightly Cloud SQL -> BigQuery export job (then partition dropped).
--   - Nightly derived-attribute compute (velocity_7day, etc.) via BigQuery.
--   - Ops investigations within the retention window (~24-48h).
--
-- Every row carries mapping_version_id (architecture B1, v0.6). Replay defaults
-- to the version recorded on the row being replayed, not current active.
--
-- ----------------------------------------------------------------------------
-- Partitioning: none for beta (D77 scope revised)
-- ----------------------------------------------------------------------------
-- This is a PLAIN table. It was PARTITION BY RANGE (event_date) with a fixed
-- bootstrap-created daily window, no DEFAULT partition, and no automation —
-- the same write-cliff shape Slice 30a removed from audit.events (D77), except
-- here the miss failed LOUD (batch nack), not silently. De-partitioned for
-- beta on the same disposable-rows/drop-recreate pattern (migration 0009).
-- Beta volume (~150K events/day) sits comfortably in a plain table.
--
-- Partitioning returns at Slice 21 (BQ archive + eviction), WITH automation
-- (decisions.md D29/D34). event_date stays NOT NULL + CHECK-consistent
-- (ck_ssse_event_date_matches_sale_timestamp) so that re-partition is safe.
--
-- ----------------------------------------------------------------------------
-- Phase 0 migration order (required for this DDL to succeed)
-- ----------------------------------------------------------------------------
--
-- 1. Schemas exist: canonical, identity_mirror, config.
-- 2. uuidv7() function installed.
-- 3. Target FK tables exist: identity_mirror.tenants,
--    identity_mirror.stores, config.source_mappings.
-- 4. canonical.tax_treatment_enum exists (created by the
--    store_sku_current_position DDL, or here if applied first).
-- 5. Apply this DDL.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: canonical
--   - schema: identity_mirror, with tables tenants, stores
--   - schema: config, with table source_mappings
--   - function: uuidv7()
--   - type:     canonical.tax_treatment_enum
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Table (plain for beta; Slice 21 re-partitions by event_date)
-- ----------------------------------------------------------------------------

CREATE TABLE canonical.store_sku_sale_events (

    -- ---------- Surrogate key ----------
    id                          UUID                                NOT NULL DEFAULT uuidv7(),

    -- ---------- Event date (Slice 21's re-partition key) ----------
    event_date                  DATE                                NOT NULL,

    -- ---------- Identity ----------
    tenant_id                   UUID                                NOT NULL,
    store_id                    UUID                                NOT NULL,

    -- ---------- SKU identity ----------
    sku_id                      VARCHAR(128) COLLATE "C"            NOT NULL,
    sku_variant                 VARCHAR(128) COLLATE "C"            NULL,
    sku_lot_batch               VARCHAR(128) COLLATE "C"            NULL,

    -- ---------- Event classification ----------
    event_subtype               VARCHAR(32) COLLATE "C"             NOT NULL,
        -- SALE, RETURN, VOID. Free-text for vocabulary flexibility; CHECK
        -- constrains to the current set. Add values via Alembic migration.

    -- ---------- When the sale happened at source ----------
    source_sale_timestamp       TIMESTAMPTZ                         NOT NULL,
        -- POS terminal time at the source, normalized to UTC at ingest.
        -- THE analytics anchor: ROOS reads "sales in last 7 days" using this.
        -- event_date is derived from this column at ingest.

    -- ---------- Transaction context ----------
    transaction_id              VARCHAR(128) COLLATE "C"            NULL,
        -- Source POS basket / transaction ID.
    line_item_seq               SMALLINT                            NULL,
        -- Position within the transaction (1..N).

    -- ---------- Sale facts ----------
    quantity                    NUMERIC(14, 3)                      NOT NULL,
        -- Units sold. Negative permitted for RETURN/VOID by event_subtype.
    unit_retail_price           NUMERIC(12, 4)                      NOT NULL,
        -- List/regular price per unit before discount.
    unit_sale_price             NUMERIC(12, 4)                      NOT NULL,
        -- Actual price charged per unit after discount.
    discount_amount             NUMERIC(12, 4)                      NULL,
        -- Absolute discount per line, as provided by source. NULLABLE because
        -- source may provide percent only.
    discount_pct                NUMERIC(5, 2)                       NULL,
        -- Percentage discount, as provided by source. NULLABLE because source
        -- may provide amount only.
    unit_cost                   NUMERIC(12, 4)                      NULL,
        -- Cost at sale time, if source provides. Useful for margin analytics.
    promo_identifier            VARCHAR(128) COLLATE "C"            NULL,
        -- The promotion applied to this line, if any.
    tax_amount                  NUMERIC(12, 4)                      NULL,
        -- Tax charged on this line.
    tax_treatment               canonical.tax_treatment_enum        NOT NULL,
        -- INCLUSIVE or EXCLUSIVE. Denormalized from store at sale time.
    currency                    CHAR(3)                             NOT NULL,
        -- ISO 4217 alpha code.

    -- ---------- Payment and customer ----------
    payment_method              VARCHAR(64) COLLATE "C"             NULL,
        -- CASH, CARD, WALLET, etc. Free-text for vocabulary flexibility.
    customer_token              VARCHAR(128) COLLATE "C"            NULL,
        -- Deterministic-HMAC token for the customer (per-tenant key).
        -- Tokenized at the receiver per architecture §4.24. Same customer
        -- across sales gets the same token; useful for repeat-customer
        -- analytics.

    -- ---------- Channel ----------
    sale_channel                VARCHAR(32) COLLATE "C"             NULL,
        -- POS, OMNI, ECOM, MARKETPLACE. NULL if source doesn't distinguish.

    -- ---------- Cross-reference to current position ----------
    store_sku_current_position_id  UUID                             NULL,
        -- Soft cross-reference to canonical.store_sku_current_position.id at
        -- write time. Not a FK: lifecycle independence (sale events outlive
        -- current_position rows for delisted SKUs) and bootstrap (a new
        -- SKU's first sale may be written before current_position exists).
        -- NULL when not available.

    -- ---------- Return/void linkage ----------
    related_sale_event_id       UUID                                NULL,
        -- For RETURN/VOID rows: id of the original SALE event. Soft reference
        -- (no FK, because cross-partition FK on partitioned tables is
        -- awkward in Postgres 15). NULL for SALE rows.

    -- ---------- Source event identity (D33 dedup key; D38 resolution) ----------
    source_id                   VARCHAR(128) COLLATE "C"            NOT NULL,
        -- Source registration identifier of the originating source. Matches
        -- config.source_mappings.source_id and bronze.data_ingress_events
        -- .source_id (varchar(128) COLLATE "C"). Component of the D33
        -- read-time dedup key (tenant_id, store_id, source_id,
        -- source_event_id). Consumer-injected from the ingress.ready
        -- envelope, cross-checked against the GCS path and the bronze row.
    source_event_id             VARCHAR(256) COLLATE "C"            NOT NULL,
        -- Per-source event identifier completing the D33 dedup key. Sale
        -- events use transaction_id || ':' || line_item_seq when the source
        -- supplies them; otherwise the deterministic fallback
        -- bronze_ref || ':' || chunk_row_index (redelivery-stable, NOT
        -- correction-collapsing; D65). Consumer-injected.

    -- ---------- DIS metadata (load-bearing) ----------
    mapping_version_id          BIGINT                              NOT NULL,
    trace_id                    UUID                                NOT NULL,
    dis_channel                 VARCHAR(32) COLLATE "C"             NOT NULL,
    last_updated_at             TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    -- ---------- DIS metadata (diagnostic, lineage) ----------
    ingest_metadata             JSONB                               NULL,
        -- JSONB: source_name, source_event_timestamp,
        -- dis_received_timestamp, dis_published_timestamp, csv_row_num.
        -- (source_event_id moved to a first-class column, D38/0003.)

    -- ---------- Primary key ----------
    CONSTRAINT pk_ssse
        PRIMARY KEY (id),
        -- (id, event_date) while partitioned — the composite existed only to
        -- satisfy the partition-key-in-PK requirement (the D77 PK precedent).

    -- ---------- Foreign keys ----------
    CONSTRAINT fk_ssse_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_ssse_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT fk_ssse_mapping_version
        FOREIGN KEY (mapping_version_id)
        REFERENCES config.source_mappings (mapping_version_id),

    -- ---------- Check constraints ----------
    CONSTRAINT ck_ssse_event_subtype_vocab
        CHECK (event_subtype IN ('SALE', 'RETURN', 'VOID')),

    CONSTRAINT ck_ssse_unit_retail_price_non_negative
        CHECK (unit_retail_price >= 0),

    CONSTRAINT ck_ssse_unit_sale_price_non_negative
        CHECK (unit_sale_price >= 0),

    CONSTRAINT ck_ssse_unit_sale_price_le_retail
        CHECK (unit_sale_price <= unit_retail_price),

    CONSTRAINT ck_ssse_unit_cost_non_negative
        CHECK (unit_cost IS NULL OR unit_cost >= 0),

    CONSTRAINT ck_ssse_discount_amount_non_negative
        CHECK (discount_amount IS NULL OR discount_amount >= 0),

    CONSTRAINT ck_ssse_discount_pct_range
        CHECK (discount_pct IS NULL
            OR (discount_pct >= 0 AND discount_pct <= 100)),

    CONSTRAINT ck_ssse_tax_amount_non_negative
        CHECK (tax_amount IS NULL OR tax_amount >= 0),

    CONSTRAINT ck_ssse_line_item_seq_positive
        CHECK (line_item_seq IS NULL OR line_item_seq > 0),

    CONSTRAINT ck_ssse_return_void_quantity_sign
        CHECK (
            (event_subtype = 'SALE' AND quantity > 0)
            OR (event_subtype IN ('RETURN', 'VOID') AND quantity < 0)
        ),

    CONSTRAINT ck_ssse_event_date_matches_sale_timestamp
        CHECK (event_date = (source_sale_timestamp AT TIME ZONE 'UTC')::date)

);


-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------

-- Primary analytics access pattern: per-SKU sales over a time window.
-- velocity_7day daily compute reads via this index.
CREATE INDEX ix_ssse_tenant_store_sku_time
    ON canonical.store_sku_sale_events
    (tenant_id, store_id, sku_id, source_sale_timestamp);

-- Store-level analytics across all SKUs.
CREATE INDEX ix_ssse_tenant_store_time
    ON canonical.store_sku_sale_events
    (tenant_id, store_id, source_sale_timestamp);

-- Cross-tenant time-range queries (ops investigations).
CREATE INDEX ix_ssse_source_sale_timestamp
    ON canonical.store_sku_sale_events
    (source_sale_timestamp);

-- Basket reconstruction by transaction.
CREATE INDEX ix_ssse_transaction_id
    ON canonical.store_sku_sale_events (transaction_id)
    WHERE transaction_id IS NOT NULL;

-- D33 read-time latest-wins dedup window (D38/0003): partition prefix +
-- event-time ordering for ROW_NUMBER() OVER (PARTITION BY tenant_id,
-- store_id, source_id, source_event_id ORDER BY source_sale_timestamp DESC, ...).
CREATE INDEX ix_ssse_dedup_key
    ON canonical.store_sku_sale_events
    (tenant_id, store_id, source_id, source_event_id, source_sale_timestamp DESC);

-- Audit lookups.
CREATE INDEX ix_ssse_trace_id
    ON canonical.store_sku_sale_events (trace_id);

-- B1 dispute and replay investigations.
CREATE INDEX ix_ssse_mapping_version
    ON canonical.store_sku_sale_events (mapping_version_id);

-- Return-to-sale linkage.
CREATE INDEX ix_ssse_related_sale_event_id
    ON canonical.store_sku_sale_events (related_sale_event_id)
    WHERE related_sale_event_id IS NOT NULL;

-- Navigate from current_position row to its sale events.
CREATE INDEX ix_ssse_current_position
    ON canonical.store_sku_sale_events (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;


-- ----------------------------------------------------------------------------
-- Trigger: keep last_updated_at fresh on UPDATE
--
-- Sale events are conceptually append-only; updates are rare (out-of-band
-- corrections, replay-with-fix). Trigger kept for consistency with the rest
-- of canonical and to keep the column honest if updates do happen.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION canonical.set_ssse_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ssse_set_last_updated_at
    BEFORE UPDATE ON canonical.store_sku_sale_events
    FOR EACH ROW
    EXECUTE FUNCTION canonical.set_ssse_last_updated_at();


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- Same posture as store_sku_current_position: enabled, forced, single policy
-- on tenant_id. Application sets app.tenant_id via SET LOCAL per transaction.
-- ----------------------------------------------------------------------------

ALTER TABLE canonical.store_sku_sale_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical.store_sku_sale_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON canonical.store_sku_sale_events
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

COMMENT ON TABLE canonical.store_sku_sale_events IS
'Append-only event log of every sale line-item, return, and void. Plain (non-partitioned) for beta; Slice 21 re-partitions by event_date for BQ archive + eviction. Written atomically with store_sku_current_position upserts by the streaming consumer. Tenant-isolated via RLS. The highest-volume table in canonical.';

COMMENT ON COLUMN canonical.store_sku_sale_events.id IS
'Surrogate identifier. UUIDv7. Primary key.';

COMMENT ON COLUMN canonical.store_sku_sale_events.event_date IS
'DATE derived from source_sale_timestamp::date at UTC. CHECK constraint enforces the derivation. Slice 21''s re-partition key.';

COMMENT ON COLUMN canonical.store_sku_sale_events.tenant_id IS
'Tenant owning this row. FK to identity_mirror.tenants. RLS scopes every read and write by this column.';

COMMENT ON COLUMN canonical.store_sku_sale_events.store_id IS
'Store where the sale occurred. FK to identity_mirror.stores.';

COMMENT ON COLUMN canonical.store_sku_sale_events.sku_id IS
'SKU identifier as received from the source. Same trimming/casing normalization rules as in store_sku_current_position.';

COMMENT ON COLUMN canonical.store_sku_sale_events.event_subtype IS
'Distinguishes SALE from RETURN and VOID. CHECK constraint enforces the closed vocabulary. Drives correct analytics: returns are not negative sales; subtype is the explicit signal.';

COMMENT ON COLUMN canonical.store_sku_sale_events.source_sale_timestamp IS
'When the sale happened at the source POS terminal, normalized to UTC at ingest. The analytics anchor for velocity_7day and every "sales in last N days" query.';

COMMENT ON COLUMN canonical.store_sku_sale_events.transaction_id IS
'Source POS basket / transaction identifier. Used to reconstruct a basket by grouping rows with the same transaction_id.';

COMMENT ON COLUMN canonical.store_sku_sale_events.line_item_seq IS
'Position of this line within the transaction (1..N). Useful for basket reconstruction; SMALLINT cap of 32767 is far above realistic basket sizes.';

COMMENT ON COLUMN canonical.store_sku_sale_events.quantity IS
'Units sold on this line. Positive for SALE; negative for RETURN and VOID. Enforced by ck_ssse_return_void_quantity_sign.';

COMMENT ON COLUMN canonical.store_sku_sale_events.unit_retail_price IS
'List / regular / everyday price per unit before any discount. The "shelf tag" price at the time of sale.';

COMMENT ON COLUMN canonical.store_sku_sale_events.unit_sale_price IS
'Actual price charged per unit after discount. unit_sale_price <= unit_retail_price (enforced).';

COMMENT ON COLUMN canonical.store_sku_sale_events.discount_amount IS
'Absolute discount per line as provided by source. NULL when source provides percent only. Both discount_amount and discount_pct may be populated for provenance.';

COMMENT ON COLUMN canonical.store_sku_sale_events.discount_pct IS
'Percentage discount as provided by source. Range 0-100. NULL when source provides amount only.';

COMMENT ON COLUMN canonical.store_sku_sale_events.unit_cost IS
'Cost per unit at sale time, if source provides. Useful for margin analytics. NULL when source omits.';

COMMENT ON COLUMN canonical.store_sku_sale_events.promo_identifier IS
'Identifier of the promotion applied to this line, if any. Joins to a future promotions table.';

COMMENT ON COLUMN canonical.store_sku_sale_events.tax_amount IS
'Tax charged on this line. NULL when source omits.';

COMMENT ON COLUMN canonical.store_sku_sale_events.tax_treatment IS
'Whether unit_retail_price and unit_sale_price are tax-inclusive or tax-exclusive. Denormalized from store.';

COMMENT ON COLUMN canonical.store_sku_sale_events.payment_method IS
'CASH, CARD, WALLET, etc. Free-text. Captured from source for downstream analytics.';

COMMENT ON COLUMN canonical.store_sku_sale_events.customer_token IS
'Deterministic-HMAC token for the customer, per-tenant key. Tokenized at the receiver per architecture §4.24. Same customer across sales gets the same token within a tenant.';

COMMENT ON COLUMN canonical.store_sku_sale_events.sale_channel IS
'POS, OMNI, ECOM, MARKETPLACE. NULL when source does not distinguish.';

COMMENT ON COLUMN canonical.store_sku_sale_events.store_sku_current_position_id IS
'Soft cross-reference to canonical.store_sku_current_position.id at write time. Not a FK: lifecycle independence (sale events outlive current_position rows for delisted SKUs) and bootstrap (a new SKU''s first sale may be written before current_position exists). NULL when not available.';

COMMENT ON COLUMN canonical.store_sku_sale_events.related_sale_event_id IS
'For RETURN and VOID rows, the id of the original SALE event being returned or voided. Soft reference (no FK); may span partitions or may be NULL if the source does not provide it.';

COMMENT ON COLUMN canonical.store_sku_sale_events.mapping_version_id IS
'Mapping version that produced this row (architecture B1, v0.6). FK to config.source_mappings. Replay defaults to the version recorded here.';

COMMENT ON COLUMN canonical.store_sku_sale_events.trace_id IS
'End-to-end trace identifier for the chunk that produced this row. Joins to BigQuery audit_events.';

COMMENT ON COLUMN canonical.store_sku_sale_events.dis_channel IS
'Ingress channel that delivered the data (csv_upload, api, csv_erp, reverse_api).';

COMMENT ON COLUMN canonical.store_sku_sale_events.last_updated_at IS
'When this row was last touched in DIS Postgres. DB-generated. Sale events are conceptually append-only; updates are rare. Trigger refreshes this on any UPDATE.';

COMMENT ON COLUMN canonical.store_sku_sale_events.ingest_metadata IS
'JSONB diagnostic and lineage detail: source_name, source_event_timestamp, dis_received_timestamp, dis_published_timestamp, csv_row_num. Designed to evolve. (source_event_id moved to a first-class column, D38/0003.)';

COMMENT ON COLUMN canonical.store_sku_sale_events.source_id IS
'Source registration identifier of the originating source. Matches config.source_mappings.source_id and bronze.data_ingress_events.source_id (varchar(128) COLLATE C, introspected). Component of the D33 read-time dedup key (tenant_id, store_id, source_id, source_event_id). Consumer-injected from the ingress.ready envelope, cross-checked against the GCS path and the bronze row (D38 resolution).';

COMMENT ON COLUMN canonical.store_sku_sale_events.source_event_id IS
'Per-source event identifier completing the D33 dedup key. Sale events use transaction_id || '':'' || line_item_seq when the source supplies them; otherwise the deterministic fallback bronze_ref || '':'' || chunk_row_index (redelivery-stable, NOT correction-collapsing; D65). Consumer-injected (D38 resolution).';
