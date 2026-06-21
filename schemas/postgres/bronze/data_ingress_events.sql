-- ============================================================================
-- DIS bronze schema: data_ingress_events
--
-- One row per data ingress event: every CSV upload, every API/webhook payload,
-- every ERP-pushed batch, every reverse-API page fetched. This table holds the
-- METADATA of the event; the payload itself lives in GCS at the URI recorded
-- in gcs_uri. Bronze does NOT carry payload bytes.
--
-- Written by:
--   - Receivers (services/csv-ingest-worker, receiver-api, receiver-csv-erp,
--     receiver-reverse-api). The receiver inserts with processing_status =
--     'RECEIVED' or 'PUBLISHED' after Pub/Sub publish completes.
--   - Streaming consumer (services/streaming-consumer). UPDATEs
--     processing_status to PROCESSED, QUARANTINED, or FAILED when the
--     pipeline completes for this event.
--
-- Read by:
--   - Streaming consumer: fetches gcs_uri to read the payload.
--   - Quarantine drainer: looks up event metadata for quarantine rows.
--   - dis-ui-server ingress-history handler: tenant-facing view of submissions.
--   - Ops dashboards: processing_status counts, throughput, errors.
--   - Replay tooling: reconstructs events from gcs_uri + metadata.
--
-- ----------------------------------------------------------------------------
-- RLS: enabled and forced
-- ----------------------------------------------------------------------------
-- This table contains tenant-identifiable data (per-tenant ingress history).
-- Tenant operators viewing their own ingress logs must not see other tenants'
-- events. Receivers SET LOCAL app.tenant_id after auth; streaming consumer
-- SET LOCAL app.tenant_id per event; dis-ui-server SET LOCAL for tenant queries.
--
-- ----------------------------------------------------------------------------
-- Phase 0 migration order
-- ----------------------------------------------------------------------------
--
-- 1. Schemas exist: bronze, identity_mirror.
-- 2. uuidv7() function installed.
-- 3. identity_mirror.tenants and identity_mirror.stores exist.
-- 4. Apply this DDL.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: bronze
--   - schema: identity_mirror, with tables tenants, stores
--   - function: uuidv7()
-- ============================================================================


CREATE TABLE bronze.data_ingress_events (

    -- ---------- Surrogate key ----------
    id                          UUID                                NOT NULL DEFAULT uuidv7(),

    -- ---------- Identity ----------
    tenant_id                   UUID                                NOT NULL,
    store_id                    UUID                                NULL,

    -- ---------- Source classification ----------
    source_id                   VARCHAR(128) COLLATE "C"            NOT NULL,
    dis_channel                 VARCHAR(32)  COLLATE "C"            NOT NULL,

    -- ---------- Correlation ----------
    trace_id                    UUID                                NOT NULL,

    -- ---------- Payload reference (data lives in GCS) ----------
    gcs_uri                     VARCHAR(1024)                       NOT NULL,
    payload_size_bytes          BIGINT                              NULL,
    payload_sha256              CHAR(64)                            NULL,
    row_count                   INTEGER                             NULL,
    content_type                VARCHAR(64)                         NULL,
    source_payload_id           VARCHAR(256) COLLATE "C"            NULL,

    -- ---------- Mapping context (informational) ----------
    mapping_version_id          BIGINT                              NULL,
    template_id                 UUID                                NULL,

    -- ---------- Caller context ----------
    auth_principal              VARCHAR(256) COLLATE "C"            NULL,
    client_ip                   INET                                NULL,
    user_agent                  VARCHAR(256)                        NULL,

    -- ---------- Lifecycle timestamps ----------
    received_at                 TIMESTAMPTZ                         NOT NULL,
    published_at                TIMESTAMPTZ                         NULL,

    -- ---------- Lifecycle state ----------
    processing_status           VARCHAR(32) COLLATE "C"             NOT NULL DEFAULT 'RECEIVED',

    -- ---------- DIS-managed ----------
    last_updated_at             TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    -- ---------- Primary key ----------
    CONSTRAINT pk_bdie
        PRIMARY KEY (id),

    -- ---------- Foreign keys ----------
    CONSTRAINT fk_bdie_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_bdie_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id)
        ON DELETE RESTRICT,

    -- ---------- Check constraints ----------
    CONSTRAINT ck_bdie_processing_status_vocab
        CHECK (processing_status IN (
            'RECEIVED', 'PUBLISHED', 'PROCESSED', 'QUARANTINED', 'FAILED'
        )),

    CONSTRAINT ck_bdie_dis_channel_vocab
        CHECK (dis_channel IN (
            'csv_upload', 'api', 'csv_erp', 'reverse_api'
        )),

    CONSTRAINT ck_bdie_row_count_non_negative
        CHECK (row_count IS NULL OR row_count >= 0),

    CONSTRAINT ck_bdie_payload_size_non_negative
        CHECK (payload_size_bytes IS NULL OR payload_size_bytes >= 0),

    CONSTRAINT ck_bdie_published_after_received
        CHECK (published_at IS NULL OR published_at >= received_at)
);


-- ----------------------------------------------------------------------------
-- Indexes (beyond PK)
-- ----------------------------------------------------------------------------

-- Tenant operator viewing their ingress history newest-first.
CREATE INDEX ix_bdie_tenant_received_at
    ON bronze.data_ingress_events (tenant_id, received_at DESC);

-- Tenant + source filtered history.
CREATE INDEX ix_bdie_tenant_source_received_at
    ON bronze.data_ingress_events (tenant_id, source_id, received_at DESC);

-- Audit lookups by trace_id (cross-tenant for ops).
CREATE INDEX ix_bdie_trace_id
    ON bronze.data_ingress_events (trace_id);

-- Ops dashboards filtering by lifecycle state.
CREATE INDEX ix_bdie_processing_status
    ON bronze.data_ingress_events (processing_status);

-- Dedup lookups and replay tooling.
CREATE INDEX ix_bdie_gcs_uri
    ON bronze.data_ingress_events (gcs_uri);


-- ----------------------------------------------------------------------------
-- Trigger: keep last_updated_at fresh on UPDATE
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION bronze.set_bdie_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bdie_set_last_updated_at
    BEFORE UPDATE ON bronze.data_ingress_events
    FOR EACH ROW
    EXECUTE FUNCTION bronze.set_bdie_last_updated_at();


-- ----------------------------------------------------------------------------
-- Row-Level Security
-- ----------------------------------------------------------------------------

ALTER TABLE bronze.data_ingress_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE bronze.data_ingress_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON bronze.data_ingress_events
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

COMMENT ON TABLE bronze.data_ingress_events IS
'One row per data ingress event: every CSV upload, API/webhook payload, ERP-pushed batch, and reverse-API page. Holds METADATA of the event; the payload itself lives in GCS at the URI in gcs_uri. Written by receivers on accept and updated by the streaming consumer on completion. Tenant-isolated via RLS.';

COMMENT ON COLUMN bronze.data_ingress_events.id IS
'Surrogate PK. UUIDv7 generated by uuidv7() DEFAULT. Referenced by ingress.ready Pub/Sub messages as data_ingress_event_id. Downstream services (streaming consumer, quarantine drainer) use it to look up this event''s metadata.';

COMMENT ON COLUMN bronze.data_ingress_events.tenant_id IS
'The tenant whose data was submitted. Set by the receiver from the authenticated principal''s tenant claim. FK to identity_mirror.tenants. Drives RLS isolation.';

COMMENT ON COLUMN bronze.data_ingress_events.store_id IS
'The store the data belongs to. Populated by the receiver when known at receive time (csv_upload selects a store; api receivers parse from payload). NULL when store identity is deferred to the streaming consumer (multi-store CSV uploads, per-row store resolution). FK to identity_mirror.stores.';

COMMENT ON COLUMN bronze.data_ingress_events.source_id IS
'The registered source of this data (e.g., shopify_pos_v2, square_csv, manual_csv_upload). Set by the receiver. Used by the streaming consumer to look up the active mapping in config.source_mappings.';

COMMENT ON COLUMN bronze.data_ingress_events.dis_channel IS
'Which DIS receiver handled this event: csv_upload, api, csv_erp, reverse_api. Set by the receiver itself. Used by ops dashboards and downstream stage-tracking.';

COMMENT ON COLUMN bronze.data_ingress_events.trace_id IS
'End-to-end correlation identifier. Generated by the receiver at the start of request handling and propagated through every downstream stage. Joins to BigQuery audit_events for full lifecycle reconstruction. Not unique here: a replay produces a new bronze row with the original trace_id.';

COMMENT ON COLUMN bronze.data_ingress_events.gcs_uri IS
'Full gs:// URI to the payload in GCS. Pattern: gs://ithina-bronze-raw/tenant/{id}/source/{id}/yyyy=Y/mm=M/dd=D/{trace_id}.{ext}. Written by the receiver after GCS write succeeds. The streaming consumer fetches the payload using this URI.';

COMMENT ON COLUMN bronze.data_ingress_events.payload_size_bytes IS
'Size in bytes of the GCS object. Computed by the receiver after GCS write. NULL when the receiver could not determine size. Used for quota tracking, throughput monitoring, and ops investigation of unusually large/small payloads.';

COMMENT ON COLUMN bronze.data_ingress_events.payload_sha256 IS
'SHA-256 hex digest of payload bytes. Computed by the receiver during/before GCS write. Lets the streaming consumer verify payload integrity after fetch. Also lets the receiver detect exact-duplicate submissions. NULL when the receiver did not compute the hash (large binary payloads).';

COMMENT ON COLUMN bronze.data_ingress_events.row_count IS
'Number of data rows in the payload (CSV: line count excluding header; JSON: array length). Computed by the receiver during parse-or-skim. NULL when the receiver did not count rows. Used by audit reconciliation: streaming consumer reports rows processed; difference points to quarantined rows.';

COMMENT ON COLUMN bronze.data_ingress_events.content_type IS
'MIME type of the payload (text/csv, application/json, application/x-parquet, etc.). Set by the receiver from the request header or inferred. Used by the streaming consumer to dispatch the right parser.';

COMMENT ON COLUMN bronze.data_ingress_events.source_payload_id IS
'The source system''s identifier for this payload, if provided. Examples: webhook event id (api channel); upload session id (csv_upload); ERP batch number (csv_erp); reverse-api pagination cursor (reverse_api). Captured by the receiver from the source. Lets ops correlate DIS events back to source-system logs.';

COMMENT ON COLUMN bronze.data_ingress_events.mapping_version_id IS
'The mapping_version_id active for (tenant_id, source_id) at the moment this event was received. Looked up by the receiver from config.source_mappings (or by the streaming consumer if the receiver does not resolve mapping). Informational here; the load-bearing copy lives on canonical rows produced from this event. No FK to source_mappings (intentional: bronze should not enforce on a config table).';

COMMENT ON COLUMN bronze.data_ingress_events.template_id IS
'The mapping template (config.source_mappings template lineage) this payload was uploaded against, carried on csv.received and persisted here for replay lineage (Slice 8 / D71). Informational, like mapping_version_id: no FK (template_id is not FK-addressable and bronze does not enforce on config tables). NULL only on pre-Slice-8 rows; the contract requires the field on every event since.';

COMMENT ON COLUMN bronze.data_ingress_events.auth_principal IS
'The authenticated identity that submitted this event. Format depends on channel: user:{user_id} for csv_upload; api_key:{key_fingerprint} for api channel; service_account:{name} for internal calls. Set by the receiver from the auth layer. Used for ops investigation, abuse detection, audit.';

COMMENT ON COLUMN bronze.data_ingress_events.client_ip IS
'Caller''s IP address at the receiver. Captured from the request (after any proxy headers). Used for ops investigation, abuse detection, geographic analysis. NULL for internal calls without a caller IP (scheduled reverse_api pulls).';

COMMENT ON COLUMN bronze.data_ingress_events.user_agent IS
'User-Agent header from the caller. Useful for diagnosis (this tenant''s CSV upload tool is on an old version, that''s why the format is off). Captured by the receiver from the request. NULL when not provided.';

COMMENT ON COLUMN bronze.data_ingress_events.received_at IS
'Server-side timestamp when the receiver accepted the event (after auth, before GCS write). Set by the receiver. The clock for how long this event has been in the pipeline. Used in latency SLO measurement.';

COMMENT ON COLUMN bronze.data_ingress_events.published_at IS
'Server-side timestamp when the receiver published ingress.ready to Pub/Sub. Set by the receiver after Pub/Sub publish succeeds. NULL until publish completes (or never, if processing failed before publish). The handoff timestamp from receiver to streaming consumer.';

COMMENT ON COLUMN bronze.data_ingress_events.processing_status IS
'Lifecycle state. RECEIVED: receiver accepted. PUBLISHED: ingress.ready published. PROCESSED: streaming consumer completed canonical write. QUARANTINED: streaming consumer routed to quarantine. FAILED: terminal failure, manual intervention needed. Set by the receiver on insert and updated by the streaming consumer on completion. Drives ops dashboards.';

COMMENT ON COLUMN bronze.data_ingress_events.last_updated_at IS
'When this row was last touched. DEFAULT NOW() on INSERT; refreshed by BEFORE UPDATE trigger. Distinguishes DB-touched time from any source-side timestamp.';
