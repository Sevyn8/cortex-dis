-- ============================================================================
-- DIS BigQuery dataset: canonical_history
-- Table: store_sku_signal_history
--
-- Long-term archive of canonical.store_sku_signal_history from Cloud SQL.
-- Loaded daily by services/nightly-batch via Cloud SQL → GCS Parquet → BQ
-- load job (WRITE_TRUNCATE per partition for idempotency). After successful
-- load, the Cloud SQL partition is dropped.
--
-- Holds the daily-computed signals (derived attributes) per SKU per
-- as_of_date. One row per (tenant, store, sku, variant, lot, as_of_date).
-- Append-only at source; never updated.
--
-- Read by:
--   - services/daily-compute on the slow/bootstrap path (read yesterday's
--     signals when Cloud SQL retention has aged them out).
--   - services/dis-ui-server for tenant-facing time-series queries
--     ("how did velocity_7day evolve daily for SKU X?").
--   - DIS analytics consumers (ROOS-side backtesting; future).
--
-- Tenant scoping is APPLICATION-ENFORCED via libs/dis-core BqClient wrapper.
--
-- ----------------------------------------------------------------------------
-- Operational caveats (from system-level stress test)
-- ----------------------------------------------------------------------------
--
-- 1. COST GUARDS REQUIRED.
--    Configure maximum_bytes_billed per query, per-user daily quota.
--
-- 2. BqClient ENFORCEMENT REQUIRED.
--    Direct google-cloud-bigquery usage bypasses tenant scoping. CI lint
--    and CI test enforce.
--
-- 3. SOURCE FRESHNESS CHECKS REQUIRED.
--    dbt source freshness: most recent as_of_date partition must exist and
--    be no older than 25 hours. Failure indicates daily-compute or
--    nightly-batch has not completed.
--
-- 4. SCHEMA MIGRATION SEQUENCING.
--    New signal columns added via Alembic in source must propagate to
--    export job and dbt model in order. Old partitions will have NULL
--    for new signals (expected).
--
-- 5. RIGHT-TO-DELETE PLAN (forward note).
--    Per-tenant datasets is the upgrade path.
--
-- ----------------------------------------------------------------------------
-- Idempotency
-- ----------------------------------------------------------------------------
-- WRITE_TRUNCATE per partition. Signal rows are immutable in source
-- (append-only); reload of a partition produces identical data.
--
-- ----------------------------------------------------------------------------
-- Forward notes
-- ----------------------------------------------------------------------------
-- A. Cluster has only 3 columns. Signal volume is lower than event volume
--    (one row per SKU per day, not per event); 3-column cluster is sufficient.
--
-- B. Could add _load_id (UUID per load-job execution) for reconciliation.
--    Not implemented now.
--
-- C. As new signals are added, columns proliferate. If column count exceeds
--    ~30, consider splitting into "signals_core" and "signals_extended"
--    tables. Current shape is fine for v1.0 (3 signals).
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - dataset: canonical_history (must exist)
--   - source:  canonical.store_sku_signal_history in Cloud SQL Postgres
--   - export:  services/nightly-batch, loads via WRITE_TRUNCATE per partition
-- ============================================================================


CREATE TABLE `canonical_history.store_sku_signal_history`
(
    -- ---------- Surrogate key from source ----------
    id                              STRING      NOT NULL OPTIONS(description="UUIDv7 from canonical Postgres source row."),

    -- ---------- Partition key ----------
    as_of_date                      DATE        NOT NULL OPTIONS(description="Partition key. The date these signals describe (not the date the compute job ran)."),

    -- ---------- Identity (cluster keys 1-3) ----------
    tenant_id                       STRING      NOT NULL OPTIONS(description="The tenant. Cluster key 1."),
    store_id                        STRING      NOT NULL OPTIONS(description="The store. Cluster key 2."),
    sku_id                          STRING      NOT NULL OPTIONS(description="The SKU. Cluster key 3."),
    sku_variant                     STRING               OPTIONS(description="Sub-classification of SKU. NULL when not applicable."),
    sku_lot_batch                   STRING               OPTIONS(description="Lot or batch identifier. NULL when not applicable."),

    -- ---------- Cross-reference ----------
    store_sku_current_position_id   STRING               OPTIONS(description="Soft cross-reference to canonical.store_sku_current_position.id at compute time. May be NULL."),

    -- ---------- Signals ----------
    velocity_7day                   NUMERIC              OPTIONS(description="Sales velocity over trailing 7 days. Gross sales only (returns/voids excluded). Unit-of-time TBD at compute implementation."),
    stock_age_days                  INT64                OPTIONS(description="Days since most recent INVENTORY RECEIPT event for the current lot at this store. NULL when no receipt known."),
    unit_cost_trend_30day           NUMERIC              OPTIONS(description="Trend in unit_cost over trailing 30 days. Semantic TBD at compute implementation. Negative values valid (cost decreased)."),

    -- ---------- DIS metadata ----------
    trace_id                        STRING      NOT NULL OPTIONS(description="Trace identifier for the compute-job run that produced this row."),
    created_at                      TIMESTAMP   NOT NULL OPTIONS(description="When DIS wrote this row (when the daily compute job ran). Distinct from as_of_date."),
    compute_metadata                JSON                 OPTIONS(description="JSONB from source: compute job name, version, input row counts, runtime, source-data freshness boundaries."),

    -- ---------- BQ-specific metadata (ETL provenance) ----------
    _loaded_at                      TIMESTAMP   NOT NULL OPTIONS(description="When the nightly-batch load wrote this row to BQ."),
    _source_partition_date          DATE        NOT NULL OPTIONS(description="Cloud SQL Postgres partition this row was exported from. Usually equals as_of_date.")
)
PARTITION BY as_of_date
CLUSTER BY tenant_id, store_id, sku_id
OPTIONS(
    description = "Long-term archive of canonical.store_sku_signal_history. Loaded daily by services/nightly-batch via WRITE_TRUNCATE per partition. Partitioned by as_of_date; clustered by (tenant_id, store_id, sku_id). Append-only; preserves daily-computed signals for backtesting and bootstrap of daily compute job. Tenant scoping is application-enforced via libs/dis-core BqClient.",
    labels = [("system", "dis"), ("layer", "canonical_history"), ("source_table", "canonical_store_sku_signal_history")]
);
