-- ============================================================================
-- DIS quarantine schema: quarantined_chunks
--
-- One row per ingress event that failed entirely before any canonical rows
-- were produced. Examples:
--   - Mapping not found for (tenant, source).
--   - Source unrecognized.
--   - Identity unresolvable for the whole chunk.
--   - FK validation failure at chunk level.
--   - Post-mapping schema mismatch affecting every row.
--
-- A chunk that produces some failed rows and some successful rows is NOT
-- quarantined here; only the failed rows land in quarantine.quarantined_rows.
--
-- Written by services/quarantine-drainer, consuming the `quarantine` Pub/Sub
-- topic, which is published by services/streaming-consumer on chunk-level
-- failure (and by receivers on pre-ingest PII failure).
--
-- Read by:
--   - services/dis-ui-server, for the tenant-facing quarantine console.
--   - Ops investigation queries.
--
-- ----------------------------------------------------------------------------
-- RLS: enabled and forced
-- ----------------------------------------------------------------------------
-- Tenant-identifiable data. Quarantine drainer SETs app.tenant_id per row
-- before insert. dis-ui-server SETs it for tenant queries.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: quarantine
--   - schema: identity_mirror, with tables tenants, stores
--   - function: uuidv7()
-- ============================================================================


CREATE TABLE quarantine.quarantined_chunks (

    -- ---------- Surrogate key ----------
    id                          UUID                                NOT NULL DEFAULT uuidv7(),

    -- ---------- Identity ----------
    tenant_id                   UUID                                NOT NULL,
    store_id                    UUID                                NULL,

    -- ---------- Source references ----------
    data_ingress_event_id       UUID                                NOT NULL,
    trace_id                    UUID                                NOT NULL,
    source_id                   VARCHAR(128) COLLATE "C"            NOT NULL,
    dis_channel                 VARCHAR(32)  COLLATE "C"            NOT NULL,
    gcs_uri                     VARCHAR(1024)                       NOT NULL,

    -- ---------- Failure context ----------
    failure_stage               VARCHAR(64) COLLATE "C"             NOT NULL,
    failure_reason              VARCHAR(256)                        NOT NULL,
    failure_context             JSONB                               NULL,
    mapping_version_id          BIGINT                              NULL,
    row_count_in_chunk          INTEGER                             NULL,

    -- ---------- Lifecycle ----------
    quarantined_at              TIMESTAMPTZ                         NOT NULL,
    status                      VARCHAR(32) COLLATE "C"             NOT NULL DEFAULT 'NEW',
    resolution_note             VARCHAR(1024)                       NULL,
    resolved_at                 TIMESTAMPTZ                         NULL,
    resolved_by_user_id         UUID                                NULL,

    -- ---------- DIS-managed ----------
    last_updated_at             TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    -- ---------- Primary key ----------
    CONSTRAINT pk_qc
        PRIMARY KEY (id),

    -- ---------- Foreign keys ----------
    CONSTRAINT fk_qc_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_qc_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id)
        ON DELETE RESTRICT,

    -- ---------- Check constraints ----------
    CONSTRAINT ck_qc_status_vocab
        CHECK (status IN ('NEW', 'RESOLVED', 'DISMISSED')),

    CONSTRAINT ck_qc_dis_channel_vocab
        CHECK (dis_channel IN (
            'csv_upload', 'api', 'csv_erp', 'reverse_api'
        )),

    CONSTRAINT ck_qc_failure_stage_vocab
        CHECK (failure_stage IN (
            'PRE_INGEST_PII', 'BRONZE_WRITE', 'MAPPING_LOOKUP',
            'IDENTITY_VALIDATION', 'PRE_MAPPING_VALIDATION',
            'MAPPING_EXECUTION', 'POST_MAPPING_VALIDATION',
            'CANONICAL_WRITE', 'OTHER'
        )),

    CONSTRAINT ck_qc_row_count_non_negative
        CHECK (row_count_in_chunk IS NULL OR row_count_in_chunk >= 0),

    CONSTRAINT ck_qc_resolved_consistency
        CHECK (
            (status = 'NEW' AND resolved_at IS NULL)
            OR
            (status IN ('RESOLVED', 'DISMISSED') AND resolved_at IS NOT NULL)
        )
);


-- ----------------------------------------------------------------------------
-- Indexes (beyond PK)
-- ----------------------------------------------------------------------------

CREATE INDEX ix_qc_tenant_quarantined_at
    ON quarantine.quarantined_chunks (tenant_id, quarantined_at DESC);

CREATE INDEX ix_qc_tenant_status_quarantined_at
    ON quarantine.quarantined_chunks (tenant_id, status, quarantined_at DESC);

CREATE INDEX ix_qc_trace_id
    ON quarantine.quarantined_chunks (trace_id);

CREATE INDEX ix_qc_data_ingress_event
    ON quarantine.quarantined_chunks (data_ingress_event_id);

CREATE INDEX ix_qc_failure_stage
    ON quarantine.quarantined_chunks (failure_stage);


-- ----------------------------------------------------------------------------
-- Trigger: keep last_updated_at fresh on UPDATE
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION quarantine.set_qc_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_qc_set_last_updated_at
    BEFORE UPDATE ON quarantine.quarantined_chunks
    FOR EACH ROW
    EXECUTE FUNCTION quarantine.set_qc_last_updated_at();


-- ----------------------------------------------------------------------------
-- Row-Level Security
-- ----------------------------------------------------------------------------

ALTER TABLE quarantine.quarantined_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE quarantine.quarantined_chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON quarantine.quarantined_chunks
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

COMMENT ON TABLE quarantine.quarantined_chunks IS
'One row per ingress event that failed entirely (no canonical rows produced). Written by services/quarantine-drainer from the quarantine Pub/Sub topic. Read by services/dis-ui-server for the tenant-facing quarantine console. Tenant-isolated via RLS.';

COMMENT ON COLUMN quarantine.quarantined_chunks.id IS
'Surrogate PK. UUIDv7. Referenced by the quarantine UI to fetch failure detail.';

COMMENT ON COLUMN quarantine.quarantined_chunks.tenant_id IS
'The tenant whose chunk failed. FK to identity_mirror.tenants. Drives RLS isolation.';

COMMENT ON COLUMN quarantine.quarantined_chunks.store_id IS
'Store the chunk targeted, when known at failure time. NULL when store resolution itself was the failure or when the chunk is multi-store. FK to identity_mirror.stores.';

COMMENT ON COLUMN quarantine.quarantined_chunks.data_ingress_event_id IS
'Reference to bronze.data_ingress_events.id for this chunk. Soft reference (no FK); quarantine writes should not fail if the bronze row is missing or evicted.';

COMMENT ON COLUMN quarantine.quarantined_chunks.trace_id IS
'End-to-end correlation identifier propagated from receiver. Joins to BigQuery audit_events for full lifecycle reconstruction.';

COMMENT ON COLUMN quarantine.quarantined_chunks.source_id IS
'The registered source the chunk came from. Captured for ops investigation when the source itself is the problem.';

COMMENT ON COLUMN quarantine.quarantined_chunks.dis_channel IS
'Ingress channel: csv_upload, api, csv_erp, reverse_api. Captured at quarantine time.';

COMMENT ON COLUMN quarantine.quarantined_chunks.gcs_uri IS
'GCS URI of the raw payload. Denormalized from bronze.data_ingress_events.gcs_uri so the quarantine UI can fetch the raw chunk without joining bronze.';

COMMENT ON COLUMN quarantine.quarantined_chunks.failure_stage IS
'Pipeline stage that caused the failure. Closed vocabulary, CHECK-enforced. Subset relevant to chunk-level failures includes MAPPING_LOOKUP, IDENTITY_VALIDATION, PRE_MAPPING_VALIDATION, etc.';

COMMENT ON COLUMN quarantine.quarantined_chunks.failure_reason IS
'Short human-readable reason. Generated by the streaming consumer from a known set of failure codes. Examples: "Mapping not found for (tenant, source)", "Identity service unavailable".';

COMMENT ON COLUMN quarantine.quarantined_chunks.failure_context IS
'Structured failure detail (JSONB): error code, validation rule, expected vs actual schema, exception excerpt. Shape is failure-stage-dependent; documented in the streaming consumer.';

COMMENT ON COLUMN quarantine.quarantined_chunks.mapping_version_id IS
'The mapping version active at failure time, when applicable. NULL for failures before mapping resolution (e.g., MAPPING_LOOKUP failure itself). Informational; no FK.';

COMMENT ON COLUMN quarantine.quarantined_chunks.row_count_in_chunk IS
'Total rows in the chunk, denormalized from bronze.data_ingress_events.row_count. Used by the UI for display. NULL when not known.';

COMMENT ON COLUMN quarantine.quarantined_chunks.quarantined_at IS
'When the streaming consumer (or receiver) routed this chunk to quarantine. Set by the drainer at insert; reflects pipeline time, not insert time.';

COMMENT ON COLUMN quarantine.quarantined_chunks.status IS
'Lifecycle state. NEW: just quarantined. RESOLVED: operator fixed the issue (mapping update, source fix, reingest). DISMISSED: operator decided to ignore. DEFAULT NEW.';

COMMENT ON COLUMN quarantine.quarantined_chunks.resolution_note IS
'Free-text resolution note set by the operator on transition to RESOLVED or DISMISSED. Captures what was done and why.';

COMMENT ON COLUMN quarantine.quarantined_chunks.resolved_at IS
'When status transitioned to RESOLVED or DISMISSED. NULL while NEW. CHECK enforces consistency with status.';

COMMENT ON COLUMN quarantine.quarantined_chunks.resolved_by_user_id IS
'Customer Master user who resolved or dismissed. Not a FK (cross-DB).';

COMMENT ON COLUMN quarantine.quarantined_chunks.last_updated_at IS
'When this row was last touched in DIS Postgres. DEFAULT NOW() on INSERT; trigger refreshes on UPDATE.';
