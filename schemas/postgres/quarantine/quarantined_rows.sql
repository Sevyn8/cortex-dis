-- ============================================================================
-- DIS quarantine schema: quarantined_rows
--
-- One row per individual data row that failed within an otherwise-successful
-- chunk. Examples:
--   - Post-mapping validation rejected this row's unit_cost as negative.
--   - Foreign-key lookup for this row's store_id failed.
--   - A mandatory column was missing for this row only.
--
-- Chunks that fail entirely land in quarantine.quarantined_chunks, not here.
--
-- Written by services/quarantine-drainer, consuming the `quarantine` Pub/Sub
-- topic published by services/streaming-consumer on per-row failure.
--
-- Read by:
--   - services/dis-ui-server, for the tenant-facing quarantine console (rows view).
--   - Ops investigation queries.
--
-- ----------------------------------------------------------------------------
-- Raw row payload retention
-- ----------------------------------------------------------------------------
-- The raw failed row is NOT stored in this table. Instead, this table records
-- the GCS URI of the chunk and the row's offset within that chunk. The
-- quarantine UI fetches the chunk from GCS and extracts the specific row on
-- demand. Rationale: avoids PII at rest in Postgres, simplifies right-to-delete,
-- keeps row count in this table small. Failure context (validation error,
-- expected vs actual values) is stored inline in failure_context JSONB.
--
-- ----------------------------------------------------------------------------
-- RLS: enabled and forced
-- ----------------------------------------------------------------------------
-- Same posture as quarantined_chunks. Tenant-identifiable data.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: quarantine
--   - schema: identity_mirror, with tables tenants, stores
--   - schema: config, with table source_mappings
--   - function: uuidv7()
-- ============================================================================


CREATE TABLE quarantine.quarantined_rows (

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
    row_offset                  INTEGER                             NOT NULL,
    row_sha256                  CHAR(64)                            NULL,

    -- ---------- Failure context ----------
    failure_stage               VARCHAR(64) COLLATE "C"             NOT NULL,
    failure_reason              VARCHAR(256)                        NOT NULL,
    failure_context             JSONB                               NULL,
    mapping_version_id          BIGINT                              NOT NULL,

    -- ---------- Lifecycle ----------
    quarantined_at              TIMESTAMPTZ                         NOT NULL,
    status                      VARCHAR(32) COLLATE "C"             NOT NULL DEFAULT 'NEW',
    resolution_note             VARCHAR(1024)                       NULL,
    resolved_at                 TIMESTAMPTZ                         NULL,
    resolved_by_user_id         UUID                                NULL,

    -- ---------- DIS-managed ----------
    last_updated_at             TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    -- ---------- Primary key ----------
    CONSTRAINT pk_qr
        PRIMARY KEY (id),

    -- ---------- Foreign keys ----------
    CONSTRAINT fk_qr_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_qr_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_qr_mapping_version
        FOREIGN KEY (mapping_version_id)
        REFERENCES config.source_mappings (mapping_version_id),

    -- ---------- Check constraints ----------
    CONSTRAINT ck_qr_status_vocab
        CHECK (status IN ('NEW', 'RESOLVED', 'DISMISSED')),

    CONSTRAINT ck_qr_dis_channel_vocab
        CHECK (dis_channel IN (
            'csv_upload', 'api', 'csv_erp', 'reverse_api'
        )),

    CONSTRAINT ck_qr_failure_stage_vocab
        CHECK (failure_stage IN (
            'PRE_MAPPING_VALIDATION', 'MAPPING_EXECUTION',
            'POST_MAPPING_VALIDATION', 'IDENTITY_VALIDATION',
            'CANONICAL_WRITE', 'OTHER'
        )),

    CONSTRAINT ck_qr_row_offset_non_negative
        CHECK (row_offset >= 0),

    CONSTRAINT ck_qr_resolved_consistency
        CHECK (
            (status = 'NEW' AND resolved_at IS NULL)
            OR
            (status IN ('RESOLVED', 'DISMISSED') AND resolved_at IS NOT NULL)
        )
);


-- ----------------------------------------------------------------------------
-- Indexes (beyond PK)
-- ----------------------------------------------------------------------------

CREATE INDEX ix_qr_tenant_quarantined_at
    ON quarantine.quarantined_rows (tenant_id, quarantined_at DESC);

CREATE INDEX ix_qr_tenant_status_quarantined_at
    ON quarantine.quarantined_rows (tenant_id, status, quarantined_at DESC);

CREATE INDEX ix_qr_trace_id
    ON quarantine.quarantined_rows (trace_id);

CREATE INDEX ix_qr_data_ingress_event_row_offset
    ON quarantine.quarantined_rows (data_ingress_event_id, row_offset);

CREATE INDEX ix_qr_failure_stage
    ON quarantine.quarantined_rows (failure_stage);

CREATE INDEX ix_qr_mapping_version
    ON quarantine.quarantined_rows (mapping_version_id);


-- ----------------------------------------------------------------------------
-- Trigger: keep last_updated_at fresh on UPDATE
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION quarantine.set_qr_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_qr_set_last_updated_at
    BEFORE UPDATE ON quarantine.quarantined_rows
    FOR EACH ROW
    EXECUTE FUNCTION quarantine.set_qr_last_updated_at();


-- ----------------------------------------------------------------------------
-- Row-Level Security
-- ----------------------------------------------------------------------------

ALTER TABLE quarantine.quarantined_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE quarantine.quarantined_rows FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON quarantine.quarantined_rows
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

COMMENT ON TABLE quarantine.quarantined_rows IS
'One row per individual data row that failed within an otherwise-successful chunk. The raw failed row is NOT stored here; gcs_uri + row_offset point to it in GCS. Failure context is stored inline as JSONB. Tenant-isolated via RLS.';

COMMENT ON COLUMN quarantine.quarantined_rows.id IS
'Surrogate PK. UUIDv7.';

COMMENT ON COLUMN quarantine.quarantined_rows.tenant_id IS
'The tenant whose row failed. FK to identity_mirror.tenants. Drives RLS isolation.';

COMMENT ON COLUMN quarantine.quarantined_rows.store_id IS
'Store the row targeted, when known. NULL when store resolution failed for this row specifically. FK to identity_mirror.stores.';

COMMENT ON COLUMN quarantine.quarantined_rows.data_ingress_event_id IS
'Reference to bronze.data_ingress_events.id for the chunk this row came from. Soft reference (no FK); quarantine writes should not fail if the bronze row is missing or evicted.';

COMMENT ON COLUMN quarantine.quarantined_rows.trace_id IS
'End-to-end correlation identifier propagated from receiver. Joins to BigQuery audit_events.';

COMMENT ON COLUMN quarantine.quarantined_rows.source_id IS
'The registered source the chunk came from. Captured for ops investigation.';

COMMENT ON COLUMN quarantine.quarantined_rows.dis_channel IS
'Ingress channel: csv_upload, api, csv_erp, reverse_api.';

COMMENT ON COLUMN quarantine.quarantined_rows.gcs_uri IS
'GCS URI of the chunk holding this row. The quarantine UI fetches the chunk from GCS and extracts the specific row using row_offset.';

COMMENT ON COLUMN quarantine.quarantined_rows.row_offset IS
'0-based offset of this row within the chunk. CSV row N (excluding header) → row_offset = N. JSON array index → row_offset = index. Lets the quarantine UI fetch only this row from GCS without re-parsing the entire chunk.';

COMMENT ON COLUMN quarantine.quarantined_rows.row_sha256 IS
'SHA-256 of the raw row content as it appeared in the chunk. Lets the UI verify integrity when re-fetching from GCS. NULL when the streaming consumer did not compute it.';

COMMENT ON COLUMN quarantine.quarantined_rows.failure_stage IS
'Pipeline stage that produced the failure. Closed vocabulary, CHECK-enforced. Subset of chunk-level stages (per-row failures don''t include chunk-only stages like MAPPING_LOOKUP).';

COMMENT ON COLUMN quarantine.quarantined_rows.failure_reason IS
'Short human-readable reason. Examples: "unit_cost negative", "stock_qty missing", "store_id not in identity_mirror".';

COMMENT ON COLUMN quarantine.quarantined_rows.failure_context IS
'Structured failure detail (JSONB): which column(s) failed, expected vs actual value, validation rule reference, Pandera error structure. Shape is failure-stage-dependent.';

COMMENT ON COLUMN quarantine.quarantined_rows.mapping_version_id IS
'The mapping version that produced this row''s failure. Always populated for per-row failures (mapping is always known at this stage). FK to config.source_mappings.';

COMMENT ON COLUMN quarantine.quarantined_rows.quarantined_at IS
'When the streaming consumer routed this row to quarantine. Set by the drainer at insert; reflects pipeline time.';

COMMENT ON COLUMN quarantine.quarantined_rows.status IS
'Lifecycle state. NEW: just quarantined. RESOLVED: operator fixed and (typically) reingested. DISMISSED: operator decided to ignore. DEFAULT NEW.';

COMMENT ON COLUMN quarantine.quarantined_rows.resolution_note IS
'Free-text resolution note set by the operator on transition to RESOLVED or DISMISSED.';

COMMENT ON COLUMN quarantine.quarantined_rows.resolved_at IS
'When status transitioned to RESOLVED or DISMISSED. NULL while NEW.';

COMMENT ON COLUMN quarantine.quarantined_rows.resolved_by_user_id IS
'Customer Master user who resolved. Not a FK (cross-DB).';

COMMENT ON COLUMN quarantine.quarantined_rows.last_updated_at IS
'When this row was last touched. DEFAULT NOW() on INSERT; trigger refreshes on UPDATE.';
