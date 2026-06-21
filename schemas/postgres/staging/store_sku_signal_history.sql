-- ============================================================================
-- DIS staging schema: store_sku_signal_history
--
-- Shadow mirror of canonical.store_sku_signal_history. Created for shape
-- parity with canonical so a future "shadow compute" path could populate
-- it during STAGED rollouts. In v1.0 the daily compute job runs only
-- against canonical; staging signal_history rows are not produced. The
-- table exists to keep the staging schema a clean structural mirror of
-- canonical.
--
-- Same shape, constraints, and RLS as canonical. Constraint and index names
-- prefixed with `_st_` to distinguish from canonical when investigating
-- across schemas.
--
-- ----------------------------------------------------------------------------
-- Partitioning: none for beta (D77 scope revised)
-- ----------------------------------------------------------------------------
-- This is a PLAIN table. It was PARTITION BY RANGE (as_of_date) with a fixed
-- bootstrap-created daily window, no DEFAULT partition, and no automation —
-- the same write-cliff shape Slice 30a removed from audit.events (D77).
-- De-partitioned for beta on the same disposable-rows/drop-recreate pattern
-- (migration 0009).
--
-- Partitioning returns at Slice 21 (BQ archive + eviction), WITH automation
-- (decisions.md D29/D34). as_of_date stays NOT NULL; the natural-key UNIQUE
-- keeps as_of_date (the daily-snapshot grain, not a partition artifact), so
-- the Slice 21 re-partition is safe.
--
-- ----------------------------------------------------------------------------
-- Phase 0 migration order
-- ----------------------------------------------------------------------------
--
-- 1. Schemas exist: canonical, identity_mirror.
-- 2. uuidv7() function installed.
-- 3. Target FK tables exist: identity_mirror.tenants,
--    identity_mirror.stores.
-- 4. Apply this DDL.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: canonical
--   - schema: identity_mirror, with tables tenants, stores
--   - function: uuidv7()
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Table (plain for beta; Slice 21 re-partitions by as_of_date)
-- ----------------------------------------------------------------------------

CREATE TABLE staging.store_sku_signal_history (

    -- ---------- Surrogate key ----------
    id                              UUID                            NOT NULL DEFAULT uuidv7(),

    -- ---------- As-of date (Slice 21's re-partition key) ----------
    as_of_date                      DATE                            NOT NULL,
        -- The date these signals describe. NOT the date the compute job ran.
        -- A row with as_of_date = 2026-05-27 was computed on 2026-05-28 (the
        -- day after). created_at captures the compute date.

    -- ---------- Identity ----------
    tenant_id                       UUID                            NOT NULL,
    store_id                        UUID                            NOT NULL,

    -- ---------- SKU identity ----------
    sku_id                          VARCHAR(128) COLLATE "C"        NOT NULL,
    sku_variant                     VARCHAR(128) COLLATE "C"        NULL,
    sku_lot_batch                   VARCHAR(128) COLLATE "C"        NULL,

    -- ---------- Cross-reference to current position ----------
    store_sku_current_position_id   UUID                            NULL,
        -- Soft reference to staging.store_sku_current_position.id at the
        -- time of compute. Not a FK: lifecycle independence (signal history
        -- outlives current_position rows; current_position row may not exist
        -- at compute time for very new SKUs). NULL when not available.

    -- ---------- Signals ----------
    velocity_7day                   NUMERIC(10, 4)                  NULL,
        -- Sales velocity over trailing 7 days. Semantic: gross sales only,
        -- returns and voids excluded. Unit-of-time (per day vs total over
        -- window) TBD at compute implementation; document in compute job.
        -- Always non-negative (CHECK enforced).
    stock_age_days                  SMALLINT                        NULL,
        -- Days since most recent INVENTORY RECEIPT event for the current lot
        -- at this store. NULL when no receipt event is known.
    unit_cost_trend_30day           NUMERIC(12, 4)                  NULL,
        -- Trend in unit_cost over trailing 30 days. Semantic (average, delta
        -- vs 30 days ago, percentage change, or fitted slope) TBD at compute
        -- implementation. Negative values are valid (cost decreased).

    -- ---------- DIS metadata ----------
    trace_id                        UUID                            NOT NULL,
        -- Trace for the compute-job run that produced this row.
    created_at                      TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
        -- When DIS wrote this row (= when the daily compute job ran).
        -- Distinct from as_of_date (the date the signals describe).
    compute_metadata                JSONB                           NULL,
        -- JSONB: compute job name, version, input row counts, runtime,
        -- source-data freshness boundaries, any diagnostic info. Designed
        -- to evolve.

    -- ---------- Primary key ----------
    CONSTRAINT pk_st_sssh
        PRIMARY KEY (id),
        -- (id, as_of_date) while partitioned — the composite existed only to
        -- satisfy the partition-key-in-PK requirement (the D77 PK precedent).

    -- ---------- Natural key (one signal row per SKU per day) ----------
    CONSTRAINT uq_st_sssh_natural
        UNIQUE NULLS NOT DISTINCT
        (tenant_id, store_id, sku_id, sku_variant, sku_lot_batch, as_of_date),

    -- ---------- Foreign keys ----------
    CONSTRAINT fk_st_sssh_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_st_sssh_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    -- ---------- Check constraints ----------
    CONSTRAINT ck_st_sssh_velocity_7day_non_negative
        CHECK (velocity_7day IS NULL OR velocity_7day >= 0),

    CONSTRAINT ck_st_sssh_stock_age_days_non_negative
        CHECK (stock_age_days IS NULL OR stock_age_days >= 0),

    CONSTRAINT ck_st_sssh_as_of_date_not_future
        CHECK (as_of_date <= CURRENT_DATE)

);


-- ----------------------------------------------------------------------------
-- Indexes (beyond PK and UNIQUE which auto-create)
-- ----------------------------------------------------------------------------

-- Store-level reads: all signals for a store on a given date.
CREATE INDEX ix_st_sssh_tenant_store_as_of
    ON staging.store_sku_signal_history (tenant_id, store_id, as_of_date);

-- Cross-tenant time-range queries (ops, BQ export filtering by date).
CREATE INDEX ix_st_sssh_as_of_date
    ON staging.store_sku_signal_history (as_of_date);

-- Navigate from current_position row to its signal history.
CREATE INDEX ix_st_sssh_current_position
    ON staging.store_sku_signal_history (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;

-- Ops investigation of a specific compute-job run.
CREATE INDEX ix_st_sssh_trace_id
    ON staging.store_sku_signal_history (trace_id);


-- ----------------------------------------------------------------------------
-- No update trigger
--
-- This table is append-only by design. created_at is set by DEFAULT on INSERT.
-- UPDATEs are not expected; if one happens, it should be visible (no silent
-- re-stamping). Earlier canonical tables carry a BEFORE UPDATE trigger to
-- refresh last_updated_at; that column does not exist here.
-- ----------------------------------------------------------------------------


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- Same posture as store_sku_current_position and store_sku_sale_events:
-- enabled, forced, single policy on tenant_id. Daily compute job sets
-- app.tenant_id via SET LOCAL before inserting that tenant's rows.
-- ----------------------------------------------------------------------------

ALTER TABLE staging.store_sku_signal_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.store_sku_signal_history FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON staging.store_sku_signal_history
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

COMMENT ON TABLE staging.store_sku_signal_history IS
'Shadow mirror of canonical.store_sku_signal_history. Created for shape parity with canonical so a future shadow-compute path could populate it during STAGED rollouts. In v1.0 the daily compute job runs only against canonical; staging signal_history rows are not produced. Same shape and RLS as canonical: plain (non-partitioned) for beta; Slice 21 re-partitions by as_of_date.';

COMMENT ON COLUMN staging.store_sku_signal_history.id IS
'Surrogate identifier. UUIDv7. Primary key.';

COMMENT ON COLUMN staging.store_sku_signal_history.as_of_date IS
'The date these signals describe, not the date the compute job ran. A row with as_of_date = 2026-05-27 was typically computed on 2026-05-28. Slice 21''s re-partition key.';

COMMENT ON COLUMN staging.store_sku_signal_history.tenant_id IS
'Tenant owning this row. FK to identity_mirror.tenants. RLS scopes every read and write by this column.';

COMMENT ON COLUMN staging.store_sku_signal_history.store_id IS
'Store this signal row pertains to. FK to identity_mirror.stores.';

COMMENT ON COLUMN staging.store_sku_signal_history.store_sku_current_position_id IS
'Soft cross-reference to staging.store_sku_current_position.id at compute time. Not a FK: lifecycle independence (signal history outlives current_position rows) and bootstrap (a new SKU''s first signal may be written before current_position exists). NULL when not available.';

COMMENT ON COLUMN staging.store_sku_signal_history.velocity_7day IS
'Sales velocity over trailing 7 days. Semantic: gross sales only (returns and voids excluded). Unit-of-time (per day vs total over window) TBD at compute implementation; document in the compute job. CHECK enforces non-negative.';

COMMENT ON COLUMN staging.store_sku_signal_history.stock_age_days IS
'Days since most recent INVENTORY RECEIPT event for the current lot at this store. NULL when no receipt event is known.';

COMMENT ON COLUMN staging.store_sku_signal_history.unit_cost_trend_30day IS
'Trend in unit_cost over trailing 30 days. Semantic (average, delta vs 30 days ago, percentage change, or fitted slope) TBD at compute implementation. Negative values are valid (cost decreased); no non-negative CHECK.';

COMMENT ON COLUMN staging.store_sku_signal_history.trace_id IS
'Trace identifier for the compute-job run that produced this row. Joins to BigQuery audit_events for full lifecycle reconstruction of the compute pass.';

COMMENT ON COLUMN staging.store_sku_signal_history.created_at IS
'When DIS wrote this row, which equals when the daily compute job ran. DB-generated via DEFAULT NOW(). Distinct from as_of_date, which is the date the signals describe.';

COMMENT ON COLUMN staging.store_sku_signal_history.compute_metadata IS
'JSONB diagnostic info about the compute run: compute job name, version, input row counts, runtime, source-data freshness boundaries. Designed to evolve; new diagnostic fields land here without schema migration.';
