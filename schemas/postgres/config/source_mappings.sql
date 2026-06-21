-- ============================================================================
-- DIS config schema: source_mappings
--
-- Versioned mapping configurations per (tenant, source, template). The
-- contract between an external data source and DIS canonical. A source may
-- carry multiple named mapping templates (e.g. 'manual_csv_upload' carrying
-- sales, inventory, pricing); each template has its own version lineage
-- (Slice 14a; register decision at the commit gate). Read by the streaming
-- consumer per-lookup inside a tenant-scoped rls_session (D6 side input);
-- mapping.changed event-driven refresh is DEFERRED (Slice 10).
--
-- Every row is immutable once written. Edits create a new version row,
-- leaving the prior version intact. This is the load-bearing property for
-- B1 (architecture v0.6): canonical rows pin to the mapping_version_id that
-- produced them, and replay defaults to that pinned version.
--
-- ----------------------------------------------------------------------------
-- Status lifecycle
-- ----------------------------------------------------------------------------
--   DRAFT      : created by onboarding service, not yet promoted.
--                Pipeline ignores.
--   STAGED     : ready for shadow rollout. Pipeline reads it for shadow mode;
--                canonical writes go to staging.* schema, not canonical.*.
--   ACTIVE     : in production. Pipeline reads this for new canonical writes.
--                At most one ACTIVE per (tenant, source, template) at a time.
--   DEPRECATED : superseded. Pipeline doesn't use for new writes; kept for
--                replay of historical chunks.
--
-- ----------------------------------------------------------------------------
-- Label generation
-- ----------------------------------------------------------------------------
-- Human-readable mapping labels are generated, not stored. The view
-- config.source_mappings_v exposes a computed `label` column with the
-- pattern: {first_word_of_tenant_name}-{source_id}-v{seq}-{YYYYMMDD}.
-- Example: acme-shopify_pos_v2-v3-20260528.
-- KNOWN GAP (Slice 14a, owned by 14b): version_seq_per_source now sequences
-- per template, so two templates under one source can both render '-v1-';
-- the label does not yet incorporate the template. The view's SELECT list is
-- deliberately untouched here (14b owns surfacing the template in reads).
--
-- ----------------------------------------------------------------------------
-- RLS: ENABLED (ENABLE + FORCE, Slice 14a)
-- ----------------------------------------------------------------------------
-- The table carries tenant_id and its rows are per-tenant data, so it follows
-- the DIS principle (RLS ON wherever tenant_id exists) with the same
-- single-GUC app.tenant_id policy as the other tenant-scoped tables. The
-- prior header claimed the consumer "reads mappings across all tenants at
-- startup" — stale: the live consumer reads per-lookup inside a tenant-scoped
-- rls_session (streaming_consumer/pipeline/mapping.py, Slice 10), so no
-- cross-tenant read exists. The view below is security_invoker so it cannot
-- silently bypass the policy with owner rights. The register decision
-- correcting the old "configuration, not tenant data" comment receives its
-- D-number at the commit gate.
--
-- ----------------------------------------------------------------------------
-- Phase 0 migration order
-- ----------------------------------------------------------------------------
--
-- 1. Schemas exist: config, identity_mirror.
-- 2. uuidv7() function installed.
-- 3. identity_mirror.tenants exists.
-- 4. Apply this DDL.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: config
--   - schema: identity_mirror, with table tenants
--   - function: uuidv7()
--   - extension: btree_gist (created below; required by the EXCLUDE
--     constraint ex_csm_template_name_per_source)
-- ============================================================================


-- Required by ex_csm_template_name_per_source (gist equality on uuid/text plus
-- uuid <>). If the target environment cannot create it, this RAISES — never a
-- silent fallback to a weaker name constraint (Slice 14a operator confirm).
CREATE EXTENSION IF NOT EXISTS btree_gist;


CREATE TABLE config.source_mappings (

    -- ---------- Surrogate key ----------
    mapping_version_id              BIGSERIAL                       NOT NULL,
        -- Monotonic across all tenants and sources. FK target for canonical
        -- mapping_version_id. Sequential numbers are easier to read in audit
        -- logs than UUIDs.

    -- ---------- Identity ----------
    tenant_id                       UUID                            NOT NULL,
    source_id                       VARCHAR(128) COLLATE "C"        NOT NULL,
        -- The registered source this mapping is for (e.g., 'shopify_pos_v2',
        -- 'square_csv', 'manual_csv_upload').
    template_id                     UUID                            NOT NULL,
        -- Stable identity of the mapping template under (tenant_id,
        -- source_id). UUIDv7, minted server-side at DRAFT creation (Slice 14b
        -- write path); immutable once set (write-path enforced convention).
        -- All version rows of one template share this id.
    template_name                   TEXT COLLATE "C"                NOT NULL,
        -- Operator-set human label for the template (e.g. 'sales',
        -- 'inventory'). Editable. Unique per (tenant_id, source_id) among
        -- non-DEPRECATED rows via ex_csm_template_name_per_source.
    version_seq_per_source          SMALLINT                        NOT NULL,
        -- Per-(tenant, source, template) sequence number. Set by trigger on
        -- INSERT. The column name predates the template grain (Slice 14a)
        -- and is kept to avoid contract churn.

    -- ---------- Status ----------
    status                          TEXT COLLATE "C"                NOT NULL,
        -- DRAFT, STAGED, ACTIVE, DEPRECATED. See file header for semantics.

    -- ---------- Mapping payload ----------
    mapping_rules                   JSONB                           NOT NULL,
        -- The rename + normalize + cast + derive rules per source field.
        -- Shape is source-type dependent; documented per source type in
        -- libs/dis-mapping; validated by Pandera at streaming consumer load.

    pre_validation_suite_ref        VARCHAR(256) COLLATE "C"        NULL,
        -- Reference to the Pandera source-shape suite (module:ClassName).
        -- NULL means use default per source_id.
    post_validation_suite_ref       VARCHAR(256) COLLATE "C"        NULL,
        -- Reference to the Pandera canonical-shape suite. NULL means use
        -- default per target canonical table.

    -- ---------- Lineage ----------
    predecessor_version_id          BIGINT                          NULL,
        -- The mapping_version_id this version was edited from. NULL for the
        -- first version of a (tenant, source, template). Not a FK to itself:
        -- kept as informational for ops; no enforcement value.

    -- ---------- Lifecycle timestamps ----------
    activated_at                    TIMESTAMPTZ                     NULL,
        -- When this version transitioned to ACTIVE.
    deprecated_at                   TIMESTAMPTZ                     NULL,
        -- When this version transitioned to DEPRECATED.

    -- ---------- Authorship ----------
    created_by_user_id              UUID                            NULL,
        -- Customer Master user who created/promoted this version. Not a FK
        -- (cross-DB). NULL if produced by an automated path (e.g., default
        -- mapping seeded by onboarding service).

    -- ---------- DIS-managed ----------
    created_at                      TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    metadata                        JSONB                           NULL,
        -- Free-form notes: change description, onboarding context, ops notes.
        -- Designed to evolve.

    -- ---------- Packet axis (Slice 14d) ----------
    template_type                   TEXT COLLATE "C"                NOT NULL,
        -- snapshot | sales | inventory_change. The stored discriminator that
        -- formalises the implicit sale-vs-change inference (Slice 14d). The
        -- vocabulary lives once in code (dis_validation.TEMPLATE_TYPES), read by
        -- the field catalog, the rule-target validator, and the consumer's
        -- routing; deliberately NO enum type and NO CHECK (a lookup-table move is
        -- deferred to when the set stabilises) — enforced at the application
        -- boundary. Appended last on the live table by Alembic 0010.

    -- ---------- Primary key ----------
    CONSTRAINT pk_csm
        PRIMARY KEY (mapping_version_id),

    -- ---------- Per-(tenant, source, template) sequence uniqueness ----------
    CONSTRAINT uq_csm_seq_per_source
        UNIQUE (tenant_id, source_id, template_id, version_seq_per_source),

    -- ---------- Template-name-to-template uniqueness ----------
    -- A name maps to at most one template among non-DEPRECATED rows. EXCLUDE,
    -- not a plain unique index: version rows of ONE template legitimately
    -- share a name (ACTIVE v1 + STAGED v2 during shadow rollout), so only
    -- rows with a DIFFERENT template_id may conflict. DEPRECATED frees a name
    -- for reuse (mirrors how active-uniqueness frees the active slot).
    CONSTRAINT ex_csm_template_name_per_source
        EXCLUDE USING gist (
            tenant_id WITH =,
            source_id WITH =,
            template_name WITH =,
            template_id WITH <>
        ) WHERE (status <> 'DEPRECATED'),

    -- ---------- Foreign key ----------
    CONSTRAINT fk_csm_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    -- ---------- Check constraints ----------
    CONSTRAINT ck_csm_status_vocab
        CHECK (status IN ('DRAFT', 'STAGED', 'ACTIVE', 'DEPRECATED')),

    CONSTRAINT ck_csm_version_seq_positive
        CHECK (version_seq_per_source > 0),

    CONSTRAINT ck_csm_activated_consistency
        CHECK (
            (status NOT IN ('ACTIVE', 'DEPRECATED') AND activated_at IS NULL)
            OR
            (status IN ('ACTIVE', 'DEPRECATED') AND activated_at IS NOT NULL)
        ),

    CONSTRAINT ck_csm_deprecated_consistency
        CHECK (
            (status != 'DEPRECATED' AND deprecated_at IS NULL)
            OR
            (status = 'DEPRECATED' AND deprecated_at IS NOT NULL)
        )
);


-- ----------------------------------------------------------------------------
-- At most one ACTIVE mapping per (tenant, source, template)
-- ----------------------------------------------------------------------------

CREATE UNIQUE INDEX uq_csm_active_per_source
    ON config.source_mappings (tenant_id, source_id, template_id)
    WHERE status = 'ACTIVE';


-- ----------------------------------------------------------------------------
-- Other indexes
-- ----------------------------------------------------------------------------

-- Streaming consumer's per-lookup read: "find the active mapping for this
-- (tenant, source)". The partial unique above is the primary lookup. This
-- wider index supports filtering ops queries.
CREATE INDEX ix_csm_tenant_source_status
    ON config.source_mappings (tenant_id, source_id, status);

-- Ops: "show me all DRAFT mappings", "all DEPRECATED mappings".
CREATE INDEX ix_csm_status
    ON config.source_mappings (status);

-- Lineage navigation.
CREATE INDEX ix_csm_predecessor
    ON config.source_mappings (predecessor_version_id)
    WHERE predecessor_version_id IS NOT NULL;


-- ----------------------------------------------------------------------------
-- Trigger: auto-set version_seq_per_source on INSERT
--
-- Computes MAX(version_seq_per_source) + 1 for the (tenant_id, source_id,
-- template_id) triple. Concurrent INSERTs for the same triple serialize on
-- the uq_csm_seq_per_source unique constraint; one wins, the others retry.
--
-- Not SECURITY DEFINER: runs as the invoking role, so under RLS the MAX scan
-- sees exactly the GUC tenant's rows — which IS NEW's tenant, because the
-- tenant_isolation WITH CHECK pins NEW.tenant_id to the GUC. With the GUC
-- unset the scan sees zero rows and the INSERT itself then fails the WITH
-- CHECK (fail-closed; the error reads as a row-level security violation, not
-- "tenant unset").
--
-- The body below is byte-identical to the one in Alembic migration 0005 so
-- the fresh-bootstrap and delta paths converge (pg_get_functiondef-compared
-- by the migration test).
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION config.set_csm_version_seq()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.version_seq_per_source IS NULL OR NEW.version_seq_per_source = 0 THEN
        SELECT COALESCE(MAX(version_seq_per_source), 0) + 1
        INTO NEW.version_seq_per_source
        FROM config.source_mappings
        WHERE tenant_id = NEW.tenant_id
          AND source_id = NEW.source_id
          AND template_id = NEW.template_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_csm_set_version_seq
    BEFORE INSERT ON config.source_mappings
    FOR EACH ROW
    EXECUTE FUNCTION config.set_csm_version_seq();


-- ----------------------------------------------------------------------------
-- Row-level security (Slice 14a)
--
-- Single-GUC tenant policy, shape-matched to the other DIS tenant tables
-- (e.g. canonical.store_sku_current_position). Unset GUC ->
-- current_setting(..., true) is NULL -> tenant_id = NULL matches nothing ->
-- zero rows (fail-closed).
-- ----------------------------------------------------------------------------

ALTER TABLE config.source_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.source_mappings FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON config.source_mappings
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        OR current_setting('app.user_type', true) = 'PLATFORM'
    )
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);


-- ----------------------------------------------------------------------------
-- View: human-readable label
--
-- Computes the mapping label from tenant name + source + sequence + date.
-- Pattern: {first_word_of_tenant_name}-{source_id}-v{seq}-{YYYYMMDD}.
-- security_invoker: the view is owned by the admin role; owner-rights
-- execution would silently bypass the tenant_isolation policy for every
-- querying role (Slice 14a). SELECT list deliberately unchanged (template
-- surfacing in reads is 14b's; see the label-collision gap in the header).
-- ----------------------------------------------------------------------------

CREATE VIEW config.source_mappings_v WITH (security_invoker = true) AS
SELECT
    sm.mapping_version_id,
    sm.tenant_id,
    sm.source_id,
    sm.version_seq_per_source,
    sm.status,
    sm.mapping_rules,
    sm.pre_validation_suite_ref,
    sm.post_validation_suite_ref,
    sm.predecessor_version_id,
    sm.activated_at,
    sm.deprecated_at,
    sm.created_by_user_id,
    sm.created_at,
    sm.metadata,
    -- Generated label:
    LOWER(REGEXP_REPLACE(SPLIT_PART(t.name, ' ', 1), '[^a-zA-Z0-9]', '', 'g'))
        || '-' || sm.source_id
        || '-v' || sm.version_seq_per_source
        || '-' || TO_CHAR(sm.created_at, 'YYYYMMDD')
        AS label,
    -- Slice 14d: appended last so Alembic 0010's CREATE OR REPLACE VIEW neither
    -- reorders nor drops the existing columns.
    sm.template_type
FROM config.source_mappings sm
JOIN identity_mirror.tenants t
    ON t.tenant_id = sm.tenant_id;


-- ----------------------------------------------------------------------------
-- Comments
-- ----------------------------------------------------------------------------

COMMENT ON TABLE config.source_mappings IS
'Versioned mapping configurations per (tenant, source, template). A source may carry multiple named templates (e.g. sales, inventory, pricing), each with its own version lineage. Immutable once written; edits create new versions. The FK target for canonical.*.mapping_version_id (B1 architecture v0.6). RLS ON (ENABLE + FORCE, single-GUC tenant_isolation policy, Slice 14a): rows are per-tenant data, read by the streaming consumer per-lookup inside a tenant-scoped rls_session (D6 side input).';

COMMENT ON COLUMN config.source_mappings.mapping_version_id IS
'Monotonic surrogate identifier across all tenants and sources. BIGSERIAL. FK target for canonical mapping_version_id.';

COMMENT ON COLUMN config.source_mappings.tenant_id IS
'The tenant this mapping belongs to. FK to identity_mirror.tenants(tenant_id).';

COMMENT ON COLUMN config.source_mappings.source_id IS
'The registered source this mapping is for (e.g., shopify_pos_v2, square_csv, manual_csv_upload). COLLATE "C".';

COMMENT ON COLUMN config.source_mappings.template_id IS
'Stable identity of the mapping template under (tenant_id, source_id). UUIDv7, minted server-side at DRAFT creation (Slice 14b write path); immutable once set (write-path enforced convention, Slice 14a). All version rows of one template share this id. Pre-14a rows were backfilled with one template per (tenant, source).';

COMMENT ON COLUMN config.source_mappings.template_name IS
'Operator-set human label for the template. Editable. Unique per (tenant_id, source_id) among non-DEPRECATED rows: ex_csm_template_name_per_source rejects two different template_ids sharing a name, while version rows of one template share the name freely. Backfilled to default for pre-14a rows.';

COMMENT ON COLUMN config.source_mappings.version_seq_per_source IS
'Per-(tenant, source, template) sequence number, starting at 1. Set automatically by the trg_csm_set_version_seq trigger on INSERT if NULL or 0 is supplied. The column name predates the template grain and is kept to avoid contract churn (Slice 14a); it sequences per template. Forms part of the generated label.';

COMMENT ON COLUMN config.source_mappings.status IS
'Lifecycle status. DRAFT: not promoted. STAGED: shadow rollout (writes go to staging.*). ACTIVE: in production. DEPRECATED: superseded, retained for replay. At most one ACTIVE per (tenant, source, template) enforced by uq_csm_active_per_source.';

COMMENT ON COLUMN config.source_mappings.mapping_rules IS
'The rename + normalize + cast + derive rules per source field. JSONB; shape is source-type dependent; documented in libs/dis-mapping; validated by Pandera when the streaming consumer loads it.';

COMMENT ON COLUMN config.source_mappings.pre_validation_suite_ref IS
'Reference to the Pandera source-shape suite (module:ClassName). NULL means use default per source_id.';

COMMENT ON COLUMN config.source_mappings.post_validation_suite_ref IS
'Reference to the Pandera canonical-shape suite. NULL means use default per target canonical table.';

COMMENT ON COLUMN config.source_mappings.predecessor_version_id IS
'The mapping_version_id this version was edited from. NULL for the first version of a (tenant, source, template). Informational only (not a FK).';

COMMENT ON COLUMN config.source_mappings.activated_at IS
'When this version transitioned to ACTIVE. NULL for DRAFT and STAGED rows. NOT NULL for ACTIVE and DEPRECATED rows (CHECK enforced).';

COMMENT ON COLUMN config.source_mappings.deprecated_at IS
'When this version transitioned to DEPRECATED. NULL for all other statuses. NOT NULL only for DEPRECATED rows (CHECK enforced).';

COMMENT ON COLUMN config.source_mappings.created_by_user_id IS
'Customer Master user who created/promoted this version. Not a FK (cross-DB). NULL if produced by an automated path.';

COMMENT ON COLUMN config.source_mappings.created_at IS
'When DIS wrote this row. DEFAULT NOW() on INSERT. Used in label generation.';

COMMENT ON COLUMN config.source_mappings.metadata IS
'Free-form JSONB notes: change description, onboarding context, ops notes. Designed to evolve.';

COMMENT ON COLUMN config.source_mappings.template_type IS
'Mapping template packet axis (Slice 14d): snapshot | sales | inventory_change. Stored, not inferred. The vocabulary lives once in code (dis_validation.TEMPLATE_TYPES), read by the field catalog, the rule-target validator, and the streaming consumer''s routing; no DB enum/CHECK (a lookup-table move is deferred). Backfilled from the rule-target signature; legacy/empty mappings defaulted to sales.';

COMMENT ON VIEW config.source_mappings_v IS
'View of source_mappings with a generated human-readable label column. Label pattern: {first_word_of_tenant_name}-{source_id}-v{seq}-{YYYYMMDD}. Tenant name fetched from identity_mirror.tenants. security_invoker: executes with the rights of the querying role so the tenant_isolation policy applies (Slice 14a). Label does not yet incorporate the template (14b gap).';
