-- ============================================================================
-- DIS identity_mirror schema: stores
--
-- Local mirror of the platform_db.core.stores table from Customer Master.
-- Maintained by the mirror-sync mechanism (TBD). Holds the subset of store
-- fields that DIS needs:
--   - store_id, tenant_id, name: identity.
--   - status: lifecycle state.
--   - country, timezone: location facts (timezone is load-bearing for date
--     boundary semantics).
--   - currency, tax_treatment: source of truth for canonical denormalization.
--   - pc_* timestamps: platform-core-sourced lifecycle timestamps.
--   - mirror_synced_at: when DIS last refreshed this row.
--
-- This table is the FK target for canonical.*.store_id columns.
--
-- ----------------------------------------------------------------------------
-- Denormalization onto canonical
-- ----------------------------------------------------------------------------
-- canonical.store_sku_current_position carries currency and tax_treatment per
-- row, denormalized from the store. The values on this mirror table are the
-- authoritative source; the streaming consumer reads them at write time and
-- stamps them onto canonical rows. If a store's currency or tax_treatment
-- changes in CM (rare), canonical rows written after the change reflect the
-- new value; older rows retain the old value (historically accurate).
--
-- ----------------------------------------------------------------------------
-- RLS: not enabled
-- ----------------------------------------------------------------------------
-- Same reasoning as identity_mirror.tenants. Identity metadata is read across
-- tenants by FK validation and DIS services.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: identity_mirror
--   - table:  identity_mirror.tenants (must exist first; FK target)
--   - function: uuidv7()
-- ============================================================================


CREATE TABLE identity_mirror.stores (

    store_id            UUID                                NOT NULL,
        -- Mirrors platform_db.core.stores.id. UUIDv7.
    tenant_id           UUID                                NOT NULL,
        -- The tenant this store belongs to. Mirrors CM.
    name                VARCHAR(200)                        NOT NULL,
        -- Store display name. Mirrors CM.
    store_code          TEXT COLLATE "C"                    NULL,
        -- Customer Master's authoritative external code for the store
        -- (core.stores.store_code, e.g. 'TX-102'). Copied as-is by Mirror
        -- Sync; nullable because the source column is nullable
        -- (decisions.md D55). Readability only — never a translation bridge;
        -- the load-bearing identity is store_id (D37).
    status              TEXT COLLATE "C"                    NOT NULL,
        -- Store lifecycle status. Mirrors CM core.store_status_enum:
        -- OPENING, ACTIVE, INACTIVE, CLOSED.

    -- ---------- Location ----------
    country             TEXT COLLATE "C"                    NOT NULL,
        -- ISO country code or country name as stored in CM. CM uses TEXT;
        -- DIS mirrors as-is.
    timezone            TEXT COLLATE "C"                    NOT NULL,
        -- IANA timezone identifier (e.g., 'America/New_York', 'Asia/Kolkata').
        -- LOAD-BEARING: DIS uses this for date-boundary semantics in derived
        -- attribute compute (yesterday_retail_price clock, daily aggregates).

    -- ---------- Commercial defaults ----------
    currency            CHAR(3)                             NOT NULL,
        -- ISO 4217 alpha code. Source of truth for canonical.*.currency.
    tax_treatment       TEXT COLLATE "C"                    NOT NULL,
        -- INCLUSIVE or EXCLUSIVE. Mirrors CM core.tax_treatment_enum.
        -- Source of truth for canonical.*.tax_treatment.

    -- ---------- Platform-core-sourced timestamps ----------
    pc_created_at       TIMESTAMPTZ                         NOT NULL,
        -- When CM created this store.
    pc_updated_at       TIMESTAMPTZ                         NOT NULL,
        -- When CM last updated this store.
    pc_closed_at        TIMESTAMPTZ                         NULL,
        -- When CM closed this store, if applicable.

    -- ---------- DIS-managed ----------
    mirror_synced_at    TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),
        -- When DIS last refreshed this row from CM.

    -- ---------- Primary key ----------
    -- NOTE: the composite PK (tenant_id, store_id) enforces tenant-scoped FK
    -- integrity — canonical.* / staging.* / bronze / quarantine declare
    -- composite FKs on (tenant_id, store_id), guaranteeing a row's store
    -- belongs to its tenant. The separate uq_ims_store_id UNIQUE below keeps
    -- store_id globally unique (UUIDv7) and provides the supporting index for
    -- store_id-only lookups (e.g. identity-service resolve paths).
    CONSTRAINT pk_ims
        PRIMARY KEY (tenant_id, store_id),

    -- ---------- Global uniqueness ----------
    CONSTRAINT uq_ims_store_id
        UNIQUE (store_id),

    -- ---------- Foreign key ----------
    CONSTRAINT fk_ims_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id)
        ON DELETE RESTRICT,

    -- ---------- Check constraints ----------
    CONSTRAINT ck_ims_status_vocab
        CHECK (status IN ('OPENING', 'ACTIVE', 'INACTIVE', 'CLOSED')),

    CONSTRAINT ck_ims_tax_treatment_vocab
        CHECK (tax_treatment IN ('INCLUSIVE', 'EXCLUSIVE'))
);


-- ----------------------------------------------------------------------------
-- Indexes (beyond PK)
-- ----------------------------------------------------------------------------

-- "All stores for this tenant" queries (ops, identity-service resolves).
CREATE INDEX ix_ims_tenant
    ON identity_mirror.stores (tenant_id);

-- Filtering by status ("active stores at this tenant").
CREATE INDEX ix_ims_tenant_status
    ON identity_mirror.stores (tenant_id, status);

-- Sync-job freshness checks.
CREATE INDEX ix_ims_mirror_synced_at
    ON identity_mirror.stores (mirror_synced_at);


-- ----------------------------------------------------------------------------
-- Comments
-- ----------------------------------------------------------------------------

COMMENT ON TABLE identity_mirror.stores IS
'Local mirror of the platform_db.core.stores table from Customer Master. Maintained by the mirror-sync mechanism. FK target for canonical.*.store_id columns. Holds currency, tax_treatment, and timezone as the authoritative source for canonical denormalization. RLS not enabled.';

COMMENT ON COLUMN identity_mirror.stores.store_id IS
'Store identifier. Mirrors platform_db.core.stores.id. UUIDv7.';

COMMENT ON COLUMN identity_mirror.stores.tenant_id IS
'The tenant this store belongs to. FK to identity_mirror.tenants(tenant_id) with ON DELETE RESTRICT.';

COMMENT ON COLUMN identity_mirror.stores.name IS
'Store display name. Mirrors CM. Up to 200 chars.';

COMMENT ON COLUMN identity_mirror.stores.store_code IS
'Customer Master''s authoritative external store code (core.stores.store_code, e.g. TX-102). Copied as-is by Mirror Sync; nullable at source (D55). Readability only; the load-bearing identity is store_id (D37).';

COMMENT ON COLUMN identity_mirror.stores.status IS
'Store lifecycle status. Mirrors CM core.store_status_enum: OPENING, ACTIVE, INACTIVE, CLOSED. Stored as TEXT.';

COMMENT ON COLUMN identity_mirror.stores.country IS
'Country as stored in CM. Mirrors as-is (CM uses TEXT, format may be ISO code or country name depending on CM data).';

COMMENT ON COLUMN identity_mirror.stores.timezone IS
'IANA timezone identifier (e.g., America/New_York). Load-bearing: DIS uses this for date-boundary semantics in derived-attribute compute and for tenant-local "yesterday" semantics.';

COMMENT ON COLUMN identity_mirror.stores.currency IS
'ISO 4217 alpha code. The source of truth for canonical.*.currency; streaming consumer reads from here and stamps canonical rows.';

COMMENT ON COLUMN identity_mirror.stores.tax_treatment IS
'INCLUSIVE or EXCLUSIVE. The source of truth for canonical.*.tax_treatment; streaming consumer reads from here and stamps canonical rows.';

COMMENT ON COLUMN identity_mirror.stores.pc_created_at IS
'Platform-core-sourced: when CM created this store.';

COMMENT ON COLUMN identity_mirror.stores.pc_updated_at IS
'Platform-core-sourced: when CM last updated this store.';

COMMENT ON COLUMN identity_mirror.stores.pc_closed_at IS
'Platform-core-sourced: when CM closed this store, if applicable.';

COMMENT ON COLUMN identity_mirror.stores.mirror_synced_at IS
'When DIS last refreshed this row from CM.';
