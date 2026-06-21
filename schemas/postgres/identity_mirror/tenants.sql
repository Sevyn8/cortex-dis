-- ============================================================================
-- DIS identity_mirror schema: tenants
--
-- Local mirror of the platform_db.core.tenants table from Customer Master.
-- Maintained by the mirror-sync mechanism (Pub/Sub, periodic pull, or other;
-- mechanism TBD as a separate task). Holds the subset of tenant fields that
-- DIS needs:
--   - tenant_id, name: identity.
--   - status: lifecycle state, drives whether DIS accepts data for the tenant.
--   - pc_* timestamps: platform-core-sourced lifecycle timestamps.
--   - mirror_synced_at: when DIS last refreshed this row.
--
-- This table is the FK target for canonical.*.tenant_id columns. Without it,
-- canonical tables cannot enforce referential integrity (FK across Postgres
-- instances is not supported).
--
-- ----------------------------------------------------------------------------
-- RLS: not enabled
-- ----------------------------------------------------------------------------
-- This table holds identity metadata that DIS services need to read across
-- tenants (FK validation, ops queries, streaming consumer writes). RLS would
-- complicate every read path without adding isolation that matters here:
-- tenant identity is the FK target, not tenant-private data.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: identity_mirror
--   - function: uuidv7()
-- ============================================================================


CREATE TABLE identity_mirror.tenants (

    tenant_id           UUID                                NOT NULL,
        -- Mirrors platform_db.core.tenants.id. UUIDv7.
    name                VARCHAR(200)                        NOT NULL,
        -- Tenant display name. Mirrors CM.
    display_code        TEXT COLLATE "C"                    NULL,
        -- Customer Master's authoritative external code for the tenant
        -- (core.tenants.display_code, e.g. 'buc-ees'). Copied as-is by
        -- Mirror Sync; nullable because the source column is nullable
        -- (decisions.md D55). Readability only — never a translation bridge;
        -- the load-bearing identity is tenant_id (D37).
    status              TEXT COLLATE "C"                    NOT NULL,
        -- Tenant lifecycle status. Mirrors CM core.tenant_status_enum:
        -- ONBOARDING, TRIAL, ACTIVE, SUSPENDED, TERMINATED.
        -- Stored as TEXT, not enum, so DIS is decoupled from CM enum
        -- evolution. CHECK constrains current vocabulary; updated via
        -- Alembic if CM adds states.

    -- ---------- Platform-core-sourced timestamps ----------
    pc_created_at       TIMESTAMPTZ                         NOT NULL,
        -- When CM created this tenant.
    pc_updated_at       TIMESTAMPTZ                         NOT NULL,
        -- When CM last updated this tenant.
    pc_suspended_at     TIMESTAMPTZ                         NULL,
        -- When CM suspended this tenant, if applicable.
    pc_terminated_at    TIMESTAMPTZ                         NULL,
        -- When CM terminated this tenant, if applicable.

    -- ---------- DIS-managed ----------
    mirror_synced_at    TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),
        -- When DIS last refreshed this row from CM. Set by the mirror-sync
        -- mechanism (or DEFAULT on insert if no explicit value).

    -- ---------- Primary key ----------
    CONSTRAINT pk_imt
        PRIMARY KEY (tenant_id),

    -- ---------- Check constraints ----------
    CONSTRAINT ck_imt_status_vocab
        CHECK (status IN (
            'ONBOARDING', 'TRIAL', 'ACTIVE', 'SUSPENDED', 'TERMINATED'
        ))
);


-- ----------------------------------------------------------------------------
-- Indexes (beyond PK)
-- ----------------------------------------------------------------------------

-- Filtering by status (ops "show me active tenants" pattern).
CREATE INDEX ix_imt_status
    ON identity_mirror.tenants (status);

-- Sync-job freshness checks ("which rows haven't been synced recently?").
CREATE INDEX ix_imt_mirror_synced_at
    ON identity_mirror.tenants (mirror_synced_at);


-- ----------------------------------------------------------------------------
-- Comments
-- ----------------------------------------------------------------------------

COMMENT ON TABLE identity_mirror.tenants IS
'Local mirror of the platform_db.core.tenants table from Customer Master. Maintained by the mirror-sync mechanism. FK target for canonical.*.tenant_id columns. RLS not enabled: identity metadata is read across tenants by FK validation and DIS services.';

COMMENT ON COLUMN identity_mirror.tenants.tenant_id IS
'Tenant identifier. Mirrors platform_db.core.tenants.id. UUIDv7.';

COMMENT ON COLUMN identity_mirror.tenants.name IS
'Tenant display name. Mirrors CM. Up to 200 chars (matches CM length).';

COMMENT ON COLUMN identity_mirror.tenants.display_code IS
'Customer Master''s authoritative external tenant code (core.tenants.display_code, e.g. buc-ees). Copied as-is by Mirror Sync; nullable at source (D55). Readability only; the load-bearing identity is tenant_id (D37).';

COMMENT ON COLUMN identity_mirror.tenants.status IS
'Tenant lifecycle status. Mirrors CM core.tenant_status_enum: ONBOARDING, TRIAL, ACTIVE, SUSPENDED, TERMINATED. Stored as TEXT to decouple DIS from CM enum evolution. CHECK constrains the current vocabulary; updated via Alembic if CM adds states.';

COMMENT ON COLUMN identity_mirror.tenants.pc_created_at IS
'Platform-core-sourced: when CM created this tenant row.';

COMMENT ON COLUMN identity_mirror.tenants.pc_updated_at IS
'Platform-core-sourced: when CM last updated this tenant row.';

COMMENT ON COLUMN identity_mirror.tenants.pc_suspended_at IS
'Platform-core-sourced: when CM suspended this tenant, if applicable.';

COMMENT ON COLUMN identity_mirror.tenants.pc_terminated_at IS
'Platform-core-sourced: when CM terminated this tenant, if applicable.';

COMMENT ON COLUMN identity_mirror.tenants.mirror_synced_at IS
'When DIS last refreshed this row from CM. Set by the mirror-sync mechanism; DEFAULT NOW() on INSERT if not explicitly provided.';
