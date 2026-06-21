-- ============================================================================
-- DIS BigQuery dataset: canonical_history
-- Table: store_sku_sale_events
--
-- Long-term archive of canonical.store_sku_sale_events from Cloud SQL.
-- Loaded daily by services/nightly-batch via Cloud SQL → GCS Parquet → BQ
-- load job (WRITE_TRUNCATE per partition for idempotency). After successful
-- load, the Cloud SQL partition is dropped.
--
-- Read by:
--   - services/daily-compute on the slow/bootstrap path when yesterday's
--     signal_history row is missing in Cloud SQL.
--   - services/dis-ui-server for tenant-facing historical analytics queries.
--   - DIS analytics consumers (ROOS-side; future).
--   - DIS engineering for ad-hoc ops investigations.
--
-- Tenant scoping is APPLICATION-ENFORCED via libs/dis-core BqClient wrapper.
-- BQ has no row-level access policy on this table; the wrapper auto-injects
-- WHERE tenant_id = :tenant_id on every query. See operational caveats below.
--
-- ----------------------------------------------------------------------------
-- Operational caveats (from system-level stress test)
-- ----------------------------------------------------------------------------
--
-- 1. COST GUARDS REQUIRED.
--    A query without partition filter scans the entire table (~22TB at year 1
--    scale; ~$110 per scan at on-demand pricing). Configure:
--      - per-query: maximum_bytes_billed flag on every BqClient call.
--      - per-user: daily quota via Google Cloud Console > BigQuery > Reservations.
--      - alerting: notify on any single query exceeding $10.
--
-- 2. BqClient ENFORCEMENT REQUIRED.
--    Direct google-cloud-bigquery usage in services bypasses tenant scoping
--    AND cost guards. Defense:
--      - libs/dis-core BqClient is the only allowed BQ client in services.
--      - CI lint rejects direct google.cloud.bigquery.Client imports outside libs.
--      - CI test scans for "FROM canonical_history" not co-located with
--        "WHERE tenant_id" or BqClient usage.
--
-- 3. SOURCE FRESHNESS CHECKS REQUIRED.
--    dbt source freshness test: the most recent event_date partition must
--    exist and be no older than 25 hours. Failure indicates nightly-batch
--    has not completed or has been failing.
--
-- 4. SCHEMA MIGRATION SEQUENCING.
--    When Postgres source schema changes via Alembic, the BQ schema must
--    follow:
--      a. Alembic migration adds column in Cloud SQL canonical.
--      b. nightly-batch export job updated to project the new column.
--      c. dbt model updated to declare new column.
--      d. Deploy in order; CI gates the sequence.
--    Old BQ partitions will have NULL for the new column (expected); dbt
--    tests should handle NULL gracefully for new columns.
--
-- 5. RIGHT-TO-DELETE PLAN (forward note).
--    GDPR-style right-to-delete touches every BQ row for a tenant. v1.0
--    doesn't implement this. Upgrade path: migrate to per-tenant datasets
--    (canonical_history_tenant_{slug}.store_sku_sale_events) so deletion is
--    one DROP TABLE per tenant. Schema content does not change; only IAM
--    and dataset structure.
--
-- ----------------------------------------------------------------------------
-- Idempotency: same-id duplication prevention
-- ----------------------------------------------------------------------------
-- The export job loads each daily partition with WRITE_TRUNCATE: the load
-- replaces the entire partition atomically. Retries that re-load the same
-- partition produce no duplicates. Semantic duplicates from replay (same
-- trace_id, different id, different mapping_version_id) are NOT deduplicated
-- at the table level; handled at query time when analytics requires it.
--
-- ----------------------------------------------------------------------------
-- Forward notes
-- ----------------------------------------------------------------------------
-- A. Cluster includes event_subtype as the 4th column. event_subtype has 3
--    values; cluster discrimination at the 4th level is marginal. Could be
--    dropped if cluster column budget needs trimming for a future addition.
--
-- B. Could add _load_id (UUID per load-job execution) for fine-grained
--    reconciliation between load runs. Not implemented now; revisit if
--    load-run forensics become a real ops need.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - dataset: canonical_history (must exist)
--   - source:  canonical.store_sku_sale_events in Cloud SQL Postgres
--   - export:  services/nightly-batch, loads via WRITE_TRUNCATE per partition
-- ============================================================================


CREATE TABLE `canonical_history.store_sku_sale_events`
(
    -- ---------- Surrogate key from source ----------
    id                              STRING      NOT NULL OPTIONS(description="UUIDv7 from canonical Postgres source row."),

    -- ---------- Partition key ----------
    event_date                      DATE        NOT NULL OPTIONS(description="Partition key. Derived in source from source_sale_timestamp::date at UTC."),

    -- ---------- Identity (cluster keys 1-3) ----------
    tenant_id                       STRING      NOT NULL OPTIONS(description="The tenant. Cluster key 1. Tenant-scoping is application-enforced via BqClient."),
    store_id                        STRING      NOT NULL OPTIONS(description="The store. Cluster key 2."),
    sku_id                          STRING      NOT NULL OPTIONS(description="The SKU. Cluster key 3."),
    sku_variant                     STRING               OPTIONS(description="Sub-classification of SKU (size, flavor). NULL when not applicable."),
    sku_lot_batch                   STRING               OPTIONS(description="Lot or batch identifier when tracked. NULL when not applicable."),

    -- ---------- Event classification (cluster key 4) ----------
    event_subtype                   STRING      NOT NULL OPTIONS(description="SALE, RETURN, VOID. Cluster key 4."),

    -- ---------- Analytics anchor ----------
    source_sale_timestamp           TIMESTAMP   NOT NULL OPTIONS(description="When the sale happened at the source POS, normalized to UTC. The analytics anchor for time-series queries."),

    -- ---------- Transaction context ----------
    transaction_id                  STRING               OPTIONS(description="Source POS basket/transaction identifier."),
    line_item_seq                   INT64                OPTIONS(description="Position of this line within the transaction (1..N)."),

    -- ---------- Sale facts ----------
    quantity                        NUMERIC     NOT NULL OPTIONS(description="Units sold. Negative for RETURN/VOID per event_subtype semantics."),
    unit_retail_price               NUMERIC     NOT NULL OPTIONS(description="List price per unit before discount."),
    unit_sale_price                 NUMERIC     NOT NULL OPTIONS(description="Actual price charged per unit after discount."),
    discount_amount                 NUMERIC              OPTIONS(description="Absolute discount per line as provided by source."),
    discount_pct                    NUMERIC              OPTIONS(description="Percentage discount as provided by source. 0-100."),
    unit_cost                       NUMERIC              OPTIONS(description="Cost per unit at sale time, if source provides. For margin analytics."),
    promo_identifier                STRING               OPTIONS(description="Identifier of the promotion applied to this line."),
    tax_amount                      NUMERIC              OPTIONS(description="Tax charged on this line."),
    tax_treatment                   STRING      NOT NULL OPTIONS(description="INCLUSIVE or EXCLUSIVE. Mirror of canonical.tax_treatment_enum as string."),
    currency                        STRING      NOT NULL OPTIONS(description="ISO 4217 alpha code (3 chars)."),

    -- ---------- Payment and customer ----------
    payment_method                  STRING               OPTIONS(description="CASH, CARD, WALLET, etc. From source."),
    customer_token                  STRING               OPTIONS(description="Deterministic-HMAC token for customer. Tokenized at receiver per architecture §4.24. Per-tenant key."),

    -- ---------- Channel ----------
    sale_channel                    STRING               OPTIONS(description="POS, OMNI, ECOM, MARKETPLACE. NULL when source does not distinguish."),

    -- ---------- Cross-references ----------
    store_sku_current_position_id   STRING               OPTIONS(description="Soft cross-reference to canonical.store_sku_current_position.id at write time. May be NULL."),
    related_sale_event_id           STRING               OPTIONS(description="For RETURN/VOID: id of the original SALE event. May span partitions; may be NULL."),

    -- ---------- DIS metadata ----------
    mapping_version_id              INT64       NOT NULL OPTIONS(description="The mapping version that produced this row (architecture B1, v0.6). Joins to config.source_mappings.mapping_version_id."),
    trace_id                        STRING      NOT NULL OPTIONS(description="End-to-end trace identifier. Joins all telemetry for the event that produced this row."),
    dis_channel                     STRING      NOT NULL OPTIONS(description="Ingress channel: csv_upload, api, csv_erp, reverse_api."),

    -- ---------- Source-level diagnostic ----------
    ingest_metadata                 JSON                 OPTIONS(description="JSONB from source: source_name, source_event_id, source_event_timestamp, dis_received_timestamp, dis_published_timestamp, csv_row_num. Queryable via JSON_VALUE / JSON_EXTRACT."),

    -- ---------- BQ-specific metadata (ETL provenance) ----------
    _loaded_at                      TIMESTAMP   NOT NULL OPTIONS(description="When the nightly-batch load wrote this row to BQ. Set by the export job."),
    _source_partition_date          DATE        NOT NULL OPTIONS(description="Cloud SQL Postgres partition this row was exported from. Usually equals event_date; separates data semantics from ETL provenance for forensics.")
)
PARTITION BY event_date
CLUSTER BY tenant_id, store_id, sku_id, event_subtype
OPTIONS(
    description = "Long-term archive of canonical.store_sku_sale_events. Loaded daily by services/nightly-batch via WRITE_TRUNCATE per partition. Partitioned by event_date; clustered by (tenant_id, store_id, sku_id, event_subtype). Tenant scoping is application-enforced via libs/dis-core BqClient.",
    labels = [("system", "dis"), ("layer", "canonical_history"), ("source_table", "canonical_store_sku_sale_events")]
);
