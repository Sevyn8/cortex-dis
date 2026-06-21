-- ============================================================================
-- DIS BigQuery dataset: canonical_history
-- Table: store_sku_change_events
--
-- Long-term archive of canonical.store_sku_change_events from Cloud SQL.
-- Loaded daily by services/nightly-batch via Cloud SQL → GCS Parquet → BQ
-- load job (WRITE_TRUNCATE per partition for idempotency). After successful
-- load, the Cloud SQL partition is dropped.
--
-- Holds all non-sale change events: inventory movements, price changes, cost
-- changes, regulatory changes, status changes, catalogue changes. Polymorphic
-- structure via event_category + event_subtype.
--
-- Read by:
--   - services/daily-compute on the slow/bootstrap path (stock_age_days from
--     INVENTORY RECEIPT events; unit_cost_trend_30day from COST events).
--   - services/dis-ui-server for tenant-facing historical analytics.
--   - DIS analytics consumers (ROOS-side; future).
--   - DIS engineering for ad-hoc ops investigations.
--
-- Tenant scoping is APPLICATION-ENFORCED via libs/dis-core BqClient wrapper.
--
-- ----------------------------------------------------------------------------
-- Operational caveats (from system-level stress test)
-- ----------------------------------------------------------------------------
--
-- 1. COST GUARDS REQUIRED.
--    Configure maximum_bytes_billed per query, per-user daily quota, and
--    alerting on any single query exceeding $10.
--
-- 2. BqClient ENFORCEMENT REQUIRED.
--    Direct google-cloud-bigquery usage in services bypasses tenant scoping
--    AND cost guards. CI lint + CI test enforce.
--
-- 3. SOURCE FRESHNESS CHECKS REQUIRED.
--    dbt source freshness: most recent event_date partition must exist and
--    be no older than 25 hours.
--
-- 4. SCHEMA MIGRATION SEQUENCING.
--    Alembic migration → export job update → dbt model update. CI gates the
--    sequence. Old partitions will have NULL for new columns.
--
-- 5. RIGHT-TO-DELETE PLAN (forward note).
--    Per-tenant datasets is the upgrade path if right-to-delete becomes
--    load-bearing. Schema content unchanged.
--
-- ----------------------------------------------------------------------------
-- Idempotency
-- ----------------------------------------------------------------------------
-- WRITE_TRUNCATE per partition. Replay-induced semantic duplicates (same
-- trace_id, different id, different mapping_version_id) are NOT deduplicated
-- at the table level; handled at query time when analytics requires it.
--
-- ----------------------------------------------------------------------------
-- Forward notes
-- ----------------------------------------------------------------------------
-- A. Cluster includes event_category as the 4th column. event_category has
--    7 values; cluster discrimination at the 4th level is useful for "show
--    only INVENTORY events" filters.
--
-- B. Could add _load_id (UUID per load-job execution) for fine-grained
--    reconciliation. Not implemented now.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - dataset: canonical_history (must exist)
--   - source:  canonical.store_sku_change_events in Cloud SQL Postgres
--   - export:  services/nightly-batch, loads via WRITE_TRUNCATE per partition
-- ============================================================================


CREATE TABLE `canonical_history.store_sku_change_events`
(
    -- ---------- Surrogate key from source ----------
    id                              STRING      NOT NULL OPTIONS(description="UUIDv7 from canonical Postgres source row."),

    -- ---------- Partition key ----------
    event_date                      DATE        NOT NULL OPTIONS(description="Partition key. Derived in source from source_event_timestamp::date at UTC."),

    -- ---------- Identity (cluster keys 1-3) ----------
    tenant_id                       STRING      NOT NULL OPTIONS(description="The tenant. Cluster key 1. Tenant-scoping is application-enforced via BqClient."),
    store_id                        STRING      NOT NULL OPTIONS(description="The store. Cluster key 2."),
    sku_id                          STRING      NOT NULL OPTIONS(description="The SKU. Cluster key 3."),
    sku_variant                     STRING               OPTIONS(description="Sub-classification of SKU. NULL when not applicable."),
    sku_lot_batch                   STRING               OPTIONS(description="Lot or batch identifier. NULL when not applicable."),

    -- ---------- Cross-reference ----------
    store_sku_current_position_id   STRING               OPTIONS(description="Soft cross-reference to canonical.store_sku_current_position.id at write time. May be NULL."),

    -- ---------- Event classification (cluster key 4) ----------
    event_category                  STRING      NOT NULL OPTIONS(description="INVENTORY, PRICE, COST, REGULATORY, STATUS, CATALOGUE, OTHER. Cluster key 4. Closed vocabulary in source (CHECK-enforced in Postgres)."),
    event_subtype                   STRING      NOT NULL OPTIONS(description="Subtype within event_category (RECEIPT, RETAIL_PRICE_CHANGE, COST_CHANGE, etc.). Open vocabulary."),

    -- ---------- Timestamps ----------
    source_event_timestamp          TIMESTAMP   NOT NULL OPTIONS(description="When the source recorded the change, normalized to UTC. Analytics anchor."),
    effective_from                  TIMESTAMP            OPTIONS(description="When the change takes effect. NULL = immediately (= source_event_timestamp)."),
    effective_until                 TIMESTAMP            OPTIONS(description="When the change ends. NULL = open-ended."),

    -- ---------- Change payload ----------
    attribute_name                  STRING               OPTIONS(description="The canonical column that changed (current_retail_price, unit_cost, stock_qty, etc.). NULL when not single-column."),
    value_before                    JSON                 OPTIONS(description="Type-agnostic previous value. Shape per event_category documented in streaming consumer."),
    value_after                     JSON                 OPTIONS(description="Type-agnostic new value. Same shape rules as value_before."),

    -- ---------- Numeric shortcut columns ----------
    numeric_value_before            NUMERIC              OPTIONS(description="Typed shortcut for numeric attributes (prices, costs, stock). NULL for non-numeric changes."),
    numeric_value_after             NUMERIC              OPTIONS(description="Typed shortcut for numeric attributes."),
    numeric_change                  NUMERIC              OPTIONS(description="Signed delta. Populated for INVENTORY events (positive = added, negative = removed). NULL for PRICE/COST changes (use before/after)."),

    -- ---------- Reason ----------
    reason_code                     STRING               OPTIONS(description="Source's reason code (e.g., PO_RECEIPT, CYCLE_COUNT, DAMAGE_OUT)."),
    reason_note                     STRING               OPTIONS(description="Free-text reason or note from source. Up to 256 chars in source."),

    -- ---------- Category-specific context ----------
    change_context                  JSON                 OPTIONS(description="Event-category-specific structured context. INVENTORY: po_id, transfer_id; PRICE: pricing_zone; COST: supplier_id. Shape per category documented in streaming consumer."),

    -- ---------- DIS metadata ----------
    mapping_version_id              INT64       NOT NULL OPTIONS(description="The mapping version that produced this row (architecture B1, v0.6). Joins to config.source_mappings.mapping_version_id."),
    trace_id                        STRING      NOT NULL OPTIONS(description="End-to-end trace identifier."),
    dis_channel                     STRING      NOT NULL OPTIONS(description="Ingress channel: csv_upload, api, csv_erp, reverse_api."),

    -- ---------- Source-level diagnostic ----------
    ingest_metadata                 JSON                 OPTIONS(description="JSONB from source: source_name, source_event_id, source_event_timestamp, dis_received_timestamp, dis_published_timestamp, csv_row_num."),

    -- ---------- BQ-specific metadata (ETL provenance) ----------
    _loaded_at                      TIMESTAMP   NOT NULL OPTIONS(description="When the nightly-batch load wrote this row to BQ."),
    _source_partition_date          DATE        NOT NULL OPTIONS(description="Cloud SQL Postgres partition this row was exported from.")
)
PARTITION BY event_date
CLUSTER BY tenant_id, store_id, sku_id, event_category
OPTIONS(
    description = "Long-term archive of canonical.store_sku_change_events. Loaded daily by services/nightly-batch via WRITE_TRUNCATE per partition. Partitioned by event_date; clustered by (tenant_id, store_id, sku_id, event_category). Polymorphic structure: event_category + event_subtype classify the change. Tenant scoping is application-enforced via libs/dis-core BqClient.",
    labels = [("system", "dis"), ("layer", "canonical_history"), ("source_table", "canonical_store_sku_change_events")]
);
