-- ============================================================================
-- DIS BigQuery dataset: audit
-- Table: audit_events
--
-- End-to-end audit trail of every ingress event flowing through the DIS
-- pipeline. Emitted by every DIS service at each pipeline stage. Joins all
-- telemetry for a given trace_id across services.
--
-- Written by:
--   - services/csv-ingest-worker, receiver-api, receiver-csv-erp,
--     receiver-reverse-api (RECEIVED, PII_TOKENIZED, BRONZE_WRITTEN,
--     INGRESS_PUBLISHED).
--   - services/streaming-consumer (MAPPING_LOOKED_UP, IDENTITY_VALIDATED,
--     PRE_MAPPING_VALIDATED, MAPPING_EXECUTED, POST_MAPPING_VALIDATED,
--     CANONICAL_WRITTEN, QUARANTINED).
--   - services/daily-compute (SIGNAL_COMPUTED).
--   - services/nightly-batch (BQ_EXPORTED, PARTITION_DROPPED).
--   - services/quarantine-drainer, dis-ui-server, mirror-sync-consumer.
--
-- Read by:
--   - services/dis-ui-server audit handler (tenant-facing trace lookup).
--   - DIS engineering for ops investigations.
--   - dbt source-freshness checks on pipeline health.
--
-- Tenant scoping is APPLICATION-ENFORCED via libs/dis-core BqClient wrapper.
--
-- ----------------------------------------------------------------------------
-- Audit volume model (Option B from architecture decision)
-- ----------------------------------------------------------------------------
-- Two scopes of audit events:
--   INGRESS_EVENT-scoped: one row per stage per chunk. Records stage outcome
--                         at the chunk level (row counts, duration).
--   ROW-scoped:           one row per failed row at per-row stages.
--                         Records row-level failure detail.
--
-- For each ingress event (one CSV upload = one chunk = one bronze row):
--   - 6 INGRESS_EVENT-scoped events at chunk-level stages.
--   - 4 INGRESS_EVENT-scoped events at per-row stages (stage summaries).
--   - F ROW-scoped events where F is the row-failure count.
-- Total: 10 + F events per ingress event.
--
-- Volume scales with failure rate, not row count. Predictable and bounded.
--
-- ----------------------------------------------------------------------------
-- Operational caveats (from system-level stress test)
-- ----------------------------------------------------------------------------
--
-- 1. UNIFORM EMISSION RULE.
--    Every stage emits exactly one INGRESS_EVENT-scoped audit event, even if
--    the stage processed zero rows (e.g., all rows failed earlier and none
--    reached this stage). This maintains predictable audit-event counts per
--    ingress event and simplifies stage-completion checks. Document in
--    services that emit audit events.
--
-- 2. AUDIT EMISSION IS FIRE-AND-FORGET RETRY.
--    BQ streaming insert with bounded retries. On persistent BQ unavailability,
--    audit events may be dropped (with alert). Canonical writes do not depend
--    on audit emission success. v1 acceptable; production hardening may add
--    a Pub/Sub buffer for at-least-once delivery.
--
-- 3. REPLAY PRODUCES PARALLEL AUDIT SEQUENCES.
--    When an ingress event is replayed (e.g., after a mapping fix), the
--    streaming consumer reprocesses with potentially the same trace_id but
--    new audit-event ids. The audit table accumulates two (or more) sequences
--    for the same data_ingress_event_id. dis-ui-server's audit handler shows all
--    events ordered by event_timestamp; same data_ingress_event_id may have
--    multiple runs. Not a bug; expected behavior.
--
-- 4. RETRY DUPLICATES.
--    If a streaming consumer instance crashes mid-batch and the Pub/Sub
--    message is redelivered, some audit events from the first run may already
--    be in BQ. The retry emits new events for the same rows. Audit table
--    accepts these duplicates; dedup is at query time if needed. Bounded by
--    retry count (typically 1-3).
--
-- 5. IN-FLIGHT SIGNALING IS NOT COVERED BY AUDIT.
--    Each stage emits its INGRESS_EVENT-scoped event at STAGE COMPLETION,
--    with duration_ms populated. Stage-start signaling (for ops "is the
--    pipeline alive?") is the job of logs and metrics, not audit. If
--    a service crashes mid-stage, no audit event is emitted for that
--    incomplete stage. Acceptable; logs cover in-flight observability.
--
-- 6. COST GUARDS REQUIRED.
--    Configure maximum_bytes_billed per query (audit table is partitioned;
--    queries should always include event_date filter). Per-user daily quota.
--    Alerting on any single query exceeding $10.
--
-- 7. BqClient ENFORCEMENT REQUIRED.
--    libs/dis-core BqClient wrapper is the only allowed BQ client in services.
--    Auto-injects WHERE tenant_id = :tenant_id (for tenant-scoped audit reads).
--    Admin queries (cross-tenant ops investigation) use an explicit
--    admin-mode flag on BqClient requiring elevated auth.
--
-- 8. SOURCE FRESHNESS CHECK.
--    dbt source freshness on the most recent event_date partition: must
--    exist and be no older than 25 hours. Failure indicates the audit
--    emission pipeline has stopped.
--
-- 9. RETENTION: PERMANENT.
--    No partition expiration. Audit is the lineage record; never deleted.
--    Storage cost is bounded by audit-event volume × time. At beta scale
--    (~150K events/day for audit), year 1 storage is ~50 GB at ~$1/month.
--
-- 10. RIGHT-TO-DELETE PLAN (forward note).
--     audit_events contains tenant_id. If GDPR-style right-to-delete is
--     implemented, audit rows for a tenant must be removable. Per-tenant
--     audit datasets is the upgrade path. v1 does not implement.
--
-- ----------------------------------------------------------------------------
-- Forward notes
-- ----------------------------------------------------------------------------
-- A. Stage-start events. Currently audit emits only stage-completion events
--    with duration_ms. Adding a STARTED outcome would let ops see in-flight
--    stages. Not v1; logs/metrics cover this need.
--
-- B. Pub/Sub-buffered audit emission. v1 uses BQ streaming insert with
--    fire-and-forget retry. Production hardening may route audit through
--    Pub/Sub for at-least-once delivery and bursting buffer.
--
-- C. Replay pass identifier. When replay produces parallel audit sequences,
--    dis-ui-server currently orders by event_timestamp to show both runs. A
--    replay_pass_id column would simplify "show me only the latest replay"
--    queries. Not v1.
--
-- D. Audit-emission service. As DIS scales, consider a centralized
--    audit-emission service (services/audit-emitter) that batches and
--    deduplicates audit events before BQ write. v1 uses per-service direct
--    emission.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - dataset: audit (must exist)
--   - emitter: libs/dis-core BqClient (or a libs/dis-audit lib if it grows)
--   - reader:  services/dis-ui-server audit handler; dbt source-freshness tests
-- ============================================================================


CREATE TABLE `audit.audit_events`
(
    -- ---------- Surrogate key ----------
    id                              STRING      NOT NULL OPTIONS(description="UUIDv7 generated by the emitting service. Audit event identifier."),

    -- ---------- Partition key ----------
    event_date                      DATE        NOT NULL OPTIONS(description="Partition key. Derived from event_timestamp::date at UTC."),

    -- ---------- Time ----------
    event_timestamp                 TIMESTAMP   NOT NULL OPTIONS(description="When the stage event occurred. The analytics anchor for time-series queries on pipeline behavior."),

    -- ---------- Correlation (cluster key 4) ----------
    trace_id                        STRING      NOT NULL OPTIONS(description="End-to-end trace identifier propagated from the receiver. Joins all audit events (and canonical rows, quarantine rows, bronze rows) for one ingress event lifecycle. Cluster key 4."),

    -- ---------- Identity (cluster key 1) ----------
    tenant_id                       STRING               OPTIONS(description="The tenant this event pertains to. NULL for system-level events that precede tenant identification (e.g., pre-auth receiver errors). Cluster key 1. Tenant scoping is application-enforced via BqClient."),
    data_ingress_event_id           STRING               OPTIONS(description="Reference to bronze.data_ingress_events.id. NULL for events outside the ingress lifecycle (e.g., scheduled jobs like daily-compute and nightly-batch)."),

    -- ---------- Stage identification (cluster keys 2-3) ----------
    service_name                    STRING      NOT NULL OPTIONS(description="The DIS service that emitted this event. Cluster key 2. Vocabulary: csv_ingest_worker, receiver_api, receiver_csv_erp, receiver_reverse_api, streaming_consumer, quarantine_drainer, daily_compute, nightly_batch, dis_ui_server, mirror_sync_consumer."),
    service_version                 STRING               OPTIONS(description="Version of the service that emitted (e.g., git SHA, semantic version). For ops forensics and replay debugging."),
    stage                           STRING      NOT NULL OPTIONS(description="The pipeline stage. Cluster key 3. Vocabulary: RECEIVED, PII_TOKENIZED, BRONZE_WRITTEN, INGRESS_PUBLISHED, MAPPING_LOOKED_UP, IDENTITY_VALIDATED, PRE_MAPPING_VALIDATED, MAPPING_EXECUTED, POST_MAPPING_VALIDATED, CANONICAL_WRITTEN, QUARANTINED, SIGNAL_COMPUTED, BQ_EXPORTED, PARTITION_DROPPED, etc. Documented per service."),

    -- ---------- Scope distinguisher ----------
    event_scope                     STRING      NOT NULL OPTIONS(description="INGRESS_EVENT or ROW. INGRESS_EVENT-scoped events are per-stage summary for one chunk; ROW-scoped events are per-row records (typically failures). See Option B audit model in table comments."),

    -- ---------- Outcome ----------
    outcome                         STRING      NOT NULL OPTIONS(description="SUCCESS, FAILURE, SKIPPED, RETRIED. The stage's result for this scope."),

    -- ---------- Per-event metrics ----------
    row_count                       INT64                OPTIONS(description="Rows touched by this audit event. For INGRESS_EVENT scope at chunk-level stages, the chunk's row count. For INGRESS_EVENT scope at per-row stages, count of rows processed (success + failure). For ROW scope, always 1."),
    rows_succeeded                  INT64                OPTIONS(description="For INGRESS_EVENT scope at per-row stages: count of rows that succeeded at this stage. NULL for ROW scope and for chunk-level stages."),
    rows_failed                     INT64                OPTIONS(description="For INGRESS_EVENT scope at per-row stages: count of rows that failed at this stage. NULL for ROW scope and for chunk-level stages."),
    duration_ms                     INT64                OPTIONS(description="How long the stage took, in milliseconds. Populated mostly for INGRESS_EVENT-scoped events. NULL for ROW-scoped events."),

    -- ---------- Row-level addressing (ROW scope only) ----------
    row_offset                      INT64                OPTIONS(description="0-based offset of the failed row within the chunk. Populated only for ROW-scoped events. NULL for INGRESS_EVENT scope."),

    -- ---------- Mapping context ----------
    mapping_version_id              INT64                OPTIONS(description="Mapping version in force when this event occurred. NULL for stages that precede mapping resolution (RECEIVED, PII_TOKENIZED, BRONZE_WRITTEN, INGRESS_PUBLISHED, MAPPING_LOOKED_UP)."),

    -- ---------- Failure detail ----------
    failure_code                    STRING               OPTIONS(description="Short failure code (e.g., MAPPING_NOT_FOUND, IDENTITY_TIMEOUT, VALIDATION_FAILED, CAST_FAILED, NEGATIVE_PRICE). NULL on SUCCESS or SKIPPED."),
    failure_message                 STRING               OPTIONS(description="Human-readable failure message with detail. NULL on SUCCESS or SKIPPED."),

    -- ---------- Stage-specific structured context ----------
    event_data                      JSON                 OPTIONS(description="Stage-specific structured context. Shape per (service_name, stage) documented in each service. Examples: BRONZE_WRITTEN = {gcs_uri, payload_size_bytes, payload_sha256}; MAPPING_EXECUTED = {rules_applied, derived_columns}; CANONICAL_WRITTEN = {written_to_table, row_id, hot_table_outcome, event_table_outcome}; PRE_MAPPING_VALIDATED (ROW failure) = {column, expected_type, actual_value, pandera_error}."),

    -- ---------- Caller context (receiver stages only) ----------
    auth_principal                  STRING               OPTIONS(description="The authenticated identity. Populated by receivers (user:{user_id}, api_key:{fingerprint}, service_account:{name}). NULL for non-receiver-originated events."),
    client_ip                       STRING               OPTIONS(description="Caller's IP address. Populated by receivers (INET in Postgres mirror; STRING in BQ). NULL for non-receiver events."),

    -- ---------- BQ-specific metadata ----------
    _loaded_at                      TIMESTAMP   NOT NULL OPTIONS(description="When the audit emitter wrote this row to BQ (via streaming insert). Distinct from event_timestamp (when the stage event occurred). Useful for retry-duplicate detection and emission-latency monitoring.")
)
PARTITION BY event_date
CLUSTER BY tenant_id, service_name, stage, trace_id
OPTIONS(
    description = "End-to-end audit trail of every ingress event flowing through the DIS pipeline. Emitted by every DIS service at each pipeline stage. Partitioned by event_date; clustered by (tenant_id, service_name, stage, trace_id). Audit volume scales with failure rate, not row count (Option B: INGRESS_EVENT-scoped + ROW-scoped failures). No partition expiration (audit is permanent). Tenant scoping is application-enforced via libs/dis-core BqClient.",
    labels = [("system", "dis"), ("layer", "audit")]
);
