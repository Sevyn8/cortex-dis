-- ============================================================================
-- DIS staging schema: store_sku_current_position
--
-- Shadow mirror of canonical.store_sku_current_position. The streaming
-- consumer writes here when the active mapping for (tenant, source) has
-- status='STAGED'. Identical shape and constraints to canonical; lets the
-- operator inspect realistic output before promoting the mapping to ACTIVE.
--
-- Promotion semantics. When a STAGED mapping is promoted to ACTIVE:
--   1. New events flow to canonical (using the new ACTIVE mapping).
--   2. Old STAGED rows in staging are no longer load-bearing; cleanup job
--      removes them after a configurable retention window (default 30 days).
--
-- Same RLS posture, same FK targets (identity_mirror, config.source_mappings),
-- same trigger pattern. Constraint and index names prefixed with `_st_` to
-- distinguish from canonical when investigating across schemas in pg_indexes.
--
-- Tenant isolation is enforced by RLS; the streaming consumer sets
-- app.tenant_id at the start of every staging-write transaction.
--
-- Every row carries mapping_version_id (architecture B1, v0.6). Replay
-- defaults to the version recorded on the row being replayed.
--
-- ----------------------------------------------------------------------------
-- Phase 0 migration order (required for this DDL to succeed)
-- ----------------------------------------------------------------------------
--
-- 1. Create schemas in the DIS database:
--      CREATE SCHEMA staging;
--      CREATE SCHEMA identity_mirror;
--      CREATE SCHEMA config;
--
-- 2. Install the project-wide UUIDv7 generator function in the DIS database.
--    Same function definition as Customer Master. Schema placement TBD
--    (public or a DIS-internal schema); this DDL calls it unqualified, so
--    whichever schema it lives in must be on the search_path, or this DDL
--    needs to be updated to schema-qualify the call.
--
-- 3. Create the target tables this DDL FKs to:
--      - identity_mirror.tenants (tenant_id PK)
--      - identity_mirror.stores  (composite (tenant_id, store_id) PK)
--      - config.source_mappings        (mapping_version_id PK)
--
-- 4. Apply this DDL to create staging.store_sku_current_position.
--
-- ----------------------------------------------------------------------------
-- Dependencies (must exist before this DDL runs)
-- ----------------------------------------------------------------------------
--   - schema: canonical
--   - schema: identity_mirror, with tables tenants, stores
--   - schema: config, with table source_mappings
--   - function: uuidv7() (project-wide UUIDv7 generator)
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types
-- ----------------------------------------------------------------------------

CREATE TYPE staging.tax_treatment_enum AS ENUM (
    'INCLUSIVE',
    'EXCLUSIVE'
);

CREATE TYPE staging.expiry_source_enum AS ENUM (
    'PRINTED',
    'SCANNED',
    'ESTIMATED',
    'CV_DETECTED'
);


-- ----------------------------------------------------------------------------
-- Table
-- ----------------------------------------------------------------------------

CREATE TABLE staging.store_sku_current_position (

    -- ---------- Surrogate key ----------
    id                          UUID                                NOT NULL DEFAULT uuidv7(),

    -- ---------- Identity ----------
    tenant_id                   UUID                                NOT NULL,
    store_id                    UUID                                NOT NULL,

    -- ---------- SKU identity ----------
    sku_id                      VARCHAR(128) COLLATE "C"            NOT NULL,
    sku_variant                 VARCHAR(128) COLLATE "C"            NULL,
    sku_lot_batch               VARCHAR(128) COLLATE "C"            NULL,
    barcode                     VARCHAR(128) COLLATE "C"            NULL,

    -- ---------- Catalogue context (denormalized) ----------
    product_name                VARCHAR(128)                        NOT NULL,
    product_description         VARCHAR(128)                        NULL,
    product_category            VARCHAR(128) COLLATE "C"            NOT NULL,
    product_sub_category        VARCHAR(128) COLLATE "C"            NULL,
    product_department          VARCHAR(128) COLLATE "C"            NULL,
    supplier_id                 VARCHAR(128) COLLATE "C"            NULL,
    packaging_type              VARCHAR(128)                        NULL,
    sku_size                    NUMERIC(8, 3)                       NULL,
    unit_of_measure             VARCHAR(64)  COLLATE "C"            NULL,

    -- ---------- Operational state (native to positions) ----------
    current_retail_price        NUMERIC(12, 4)                      NOT NULL,
    unit_cost                   NUMERIC(12, 4)                      NOT NULL,
    promo_price                 NUMERIC(12, 4)                      NULL,
    promo_identifier            VARCHAR(128) COLLATE "C"            NULL,
    yesterday_retail_price      NUMERIC(12, 4)                      NULL,
    tax_treatment               staging.tax_treatment_enum        NOT NULL,
    stock_qty                   NUMERIC(14, 3)                      NULL,
    lead_time_days              SMALLINT                            NULL,
    expiry_date                 DATE                                NULL,
    receipt_date                DATE                                NULL,
    expiry_source               staging.expiry_source_enum        NULL,
    expiry_confidence           NUMERIC(3, 2)                       NULL,
    regulatory_flag             BOOLEAN                             NULL DEFAULT FALSE,
    regulatory_type             VARCHAR(128) COLLATE "C"            NULL,
    currency                    CHAR(3)                             NOT NULL,
    reorder_point               NUMERIC(14, 3)                      NULL,
    sku_status                  VARCHAR(32)  COLLATE "C"            NULL,

    -- ---------- Derived metrics ----------
    velocity_7day               NUMERIC(10, 4)                      NULL,
    stock_age_days              SMALLINT                            NULL,
    unit_cost_trend_30day       NUMERIC(12, 4)                      NULL,

    -- ---------- Staleness ----------
    attribute_staleness_map     JSONB                               NULL,

    -- ---------- DIS metadata (load-bearing) ----------
    mapping_version_id          BIGINT                              NOT NULL,
    trace_id                    UUID                                NOT NULL,
    dis_channel                 VARCHAR(32)  COLLATE "C"            NOT NULL,
    last_updated_at             TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    -- ---------- DIS metadata (diagnostic, lineage) ----------
    ingest_metadata             JSONB                               NULL,

    -- ---------- Primary key ----------
    CONSTRAINT pk_st_sscp
        PRIMARY KEY (id),

    -- ---------- Natural key ----------
    CONSTRAINT uq_st_sscp_natural
        UNIQUE NULLS NOT DISTINCT
        (tenant_id, store_id, sku_id, sku_variant, sku_lot_batch),

    -- ---------- Foreign keys ----------
    CONSTRAINT fk_st_sscp_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_st_sscp_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT fk_st_sscp_mapping_version
        FOREIGN KEY (mapping_version_id)
        REFERENCES config.source_mappings (mapping_version_id),

    -- ---------- Check constraints ----------
    CONSTRAINT ck_st_sscp_current_retail_price_non_negative
        CHECK (current_retail_price >= 0),

    CONSTRAINT ck_st_sscp_unit_cost_non_negative
        CHECK (unit_cost >= 0),

    CONSTRAINT ck_st_sscp_promo_price_non_negative
        CHECK (promo_price IS NULL OR promo_price >= 0),

    CONSTRAINT ck_st_sscp_stock_qty_non_negative
        CHECK (stock_qty IS NULL OR stock_qty >= 0),

    CONSTRAINT ck_st_sscp_lead_time_non_negative
        CHECK (lead_time_days IS NULL OR lead_time_days >= 0),

    CONSTRAINT ck_st_sscp_reorder_point_non_negative
        CHECK (reorder_point IS NULL OR reorder_point >= 0),

    CONSTRAINT ck_st_sscp_expiry_confidence_range
        CHECK (expiry_confidence IS NULL
            OR (expiry_confidence >= 0 AND expiry_confidence <= 1)),

    CONSTRAINT ck_st_sscp_expiry_triple_pairing
        CHECK (
            (expiry_date IS NULL
                AND expiry_source IS NULL
                AND expiry_confidence IS NULL)
            OR
            (expiry_date IS NOT NULL
                AND expiry_source IS NOT NULL
                AND expiry_confidence IS NOT NULL)
        ),

    CONSTRAINT ck_st_sscp_promo_identifier_requires_price
        CHECK (promo_identifier IS NULL OR promo_price IS NOT NULL)
);


-- ----------------------------------------------------------------------------
-- Indexes (beyond PK and UNIQUE which auto-create)
-- ----------------------------------------------------------------------------

CREATE INDEX ix_st_sscp_tenant_store
    ON staging.store_sku_current_position (tenant_id, store_id);

CREATE INDEX ix_st_sscp_tenant_store_category
    ON staging.store_sku_current_position (tenant_id, store_id, product_category);

CREATE INDEX ix_st_sscp_tenant_store_expiry
    ON staging.store_sku_current_position (tenant_id, store_id, expiry_date)
    WHERE expiry_date IS NOT NULL;

CREATE INDEX ix_st_sscp_last_updated_at
    ON staging.store_sku_current_position (last_updated_at);

CREATE INDEX ix_st_sscp_mapping_version
    ON staging.store_sku_current_position (mapping_version_id);

CREATE INDEX ix_st_sscp_trace_id
    ON staging.store_sku_current_position (trace_id);

CREATE INDEX ix_st_sscp_dis_channel
    ON staging.store_sku_current_position (dis_channel);


-- ----------------------------------------------------------------------------
-- Trigger: keep last_updated_at fresh on UPDATE
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION staging.set_st_sscp_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_st_sscp_set_last_updated_at
    BEFORE UPDATE ON staging.store_sku_current_position
    FOR EACH ROW
    EXECUTE FUNCTION staging.set_st_sscp_last_updated_at();


-- ----------------------------------------------------------------------------
-- Row-Level Security
-- ----------------------------------------------------------------------------

ALTER TABLE staging.store_sku_current_position ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.store_sku_current_position FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON staging.store_sku_current_position
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

COMMENT ON TABLE staging.store_sku_current_position IS
'Shadow mirror of canonical.store_sku_current_position. Written by the streaming consumer when the active mapping has status=STAGED. Identical shape to canonical; lets the operator inspect realistic output before promoting the mapping to ACTIVE. Tenant-isolated via RLS. Cleanup job removes rows after configured retention window.';

COMMENT ON COLUMN staging.store_sku_current_position.id IS
'Surrogate primary key. UUIDv7 generated by uuidv7(). Used by app code as the row reference; the natural key (tenant+store+sku+variant+lot) is enforced by uq_st_sscp_natural.';

COMMENT ON COLUMN staging.store_sku_current_position.tenant_id IS
'Tenant owning this row. FK to identity_mirror.tenants. RLS scopes all reads and writes by this column.';

COMMENT ON COLUMN staging.store_sku_current_position.store_id IS
'Store this position belongs to. Composite FK (tenant_id, store_id) to identity_mirror.stores, guaranteeing the store belongs to this row''s tenant.';

COMMENT ON COLUMN staging.store_sku_current_position.sku_id IS
'SKU identifier as received from the source. Forward note: trimming and casing normalization at ingest TBD; current expectation is byte-exact match for upserts.';

COMMENT ON COLUMN staging.store_sku_current_position.sku_variant IS
'Sub-classification of SKU when variants are tracked (size, flavor, etc.). NULL when not applicable.';

COMMENT ON COLUMN staging.store_sku_current_position.sku_lot_batch IS
'Lot or batch identifier when SKU lots are tracked separately (perishables, regulated goods). NULL when not applicable.';

COMMENT ON COLUMN staging.store_sku_current_position.barcode IS
'Barcode value, retained for traceability. Forward note: usefulness in store_sku_current_position TBD; may be removed.';

COMMENT ON COLUMN staging.store_sku_current_position.unit_cost IS
'Purchase cost per unit of sale, not per pack. Tax treatment scope TBD: confirm whether this follows the row tax_treatment column (same as retail price) or is always tax-exclusive by convention.';

COMMENT ON COLUMN staging.store_sku_current_position.promo_price IS
'Promotional price per unit of sale when an active promo applies. Same tax-treatment scope question as unit_cost.';

COMMENT ON COLUMN staging.store_sku_current_position.yesterday_retail_price IS
'Previous-day retail price for change-detection by ROOS agents. Clock semantics for "yesterday" (tenant local / store local / UTC) TBD.';

COMMENT ON COLUMN staging.store_sku_current_position.tax_treatment IS
'Whether retail prices on this row are tax-inclusive or tax-exclusive. Denormalized from store.';

COMMENT ON COLUMN staging.store_sku_current_position.regulatory_flag IS
'TRUE if this SKU is regulated. Default FALSE; NULLABLE for v1.0 to allow "unknown" state. Forward note: revisit NOT NULL once tenant onboarding policies are firm.';

COMMENT ON COLUMN staging.store_sku_current_position.regulatory_type IS
'Regulatory category when regulatory_flag = TRUE. Free-text to allow vocabulary growth (NONE, OTC, DEA_SCHEDULE, PRESCRIPTION, CANNABIS, TOBACCO, ALCOHOL, etc.).';

COMMENT ON COLUMN staging.store_sku_current_position.sku_status IS
'SKU lifecycle status at this store (ACTIVE, PAUSED, DELISTED). Free-text to allow vocabulary growth.';

COMMENT ON COLUMN staging.store_sku_current_position.velocity_7day IS
'Sales velocity over trailing 7 days. Semantics (units per day vs total over window) TBD at implementation.';

COMMENT ON COLUMN staging.store_sku_current_position.stock_age_days IS
'Days since receipt_date of the current lot. NULL when receipt_date is unknown.';

COMMENT ON COLUMN staging.store_sku_current_position.unit_cost_trend_30day IS
'Trend in unit_cost over trailing 30 days. Semantics (average, delta vs 30 days ago, percentage change, or fitted slope) TBD at implementation.';

COMMENT ON COLUMN staging.store_sku_current_position.attribute_staleness_map IS
'Per-attribute freshness map. JSONB object keyed by column name, values are ISO 8601 UTC timestamps. Initial tracked set: stock_qty, current_retail_price, unit_cost, promo_price, velocity_7day, stock_age_days, unit_cost_trend_30day. Designed to evolve. Database does not enforce shape; streaming consumer is responsible.';

COMMENT ON COLUMN staging.store_sku_current_position.mapping_version_id IS
'Mapping version that produced this row (architecture B1, v0.6). FK to config.source_mappings. Replay defaults to the version recorded here, not current active. Audit trail surfaces this on every dispute.';

COMMENT ON COLUMN staging.store_sku_current_position.trace_id IS
'End-to-end trace identifier for the chunk that produced this row''s most recent update. Joins to BigQuery audit_events for full lifecycle reconstruction.';

COMMENT ON COLUMN staging.store_sku_current_position.dis_channel IS
'Ingress channel that delivered the data (csv_upload, api, csv_erp, reverse_api). Indexed for ops dashboards.';

COMMENT ON COLUMN staging.store_sku_current_position.last_updated_at IS
'When this row was last touched in DIS Postgres. DB-generated. DEFAULT NOW() on INSERT; trigger refreshes on every UPDATE. Use for CDC, replication, change-tracking. For "data current as of" semantics, see source_event_timestamp inside ingest_metadata or attribute_staleness_map.';

COMMENT ON COLUMN staging.store_sku_current_position.ingest_metadata IS
'Diagnostic and lineage detail. JSONB object. Keys: source_name, source_event_id, source_event_timestamp, dis_received_timestamp, dis_published_timestamp, event_type, csv_row_num. Designed to evolve; new diagnostic fields land here without schema migration.';
