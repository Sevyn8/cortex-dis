-- ============================================================================
-- DIS atlas schema: schema_drafts
--
-- The Atlas console's draft/published canonical-schema IR store (A4 PR2).
-- A row holds one draft (or frozen, published) IR as a JSONB document
-- (schema_to_document(SchemaIR)); the console edits a draft and publish FREEZES
-- an immutable, versioned row.
--
-- Written by services/dis-ui-server via libs/dis-rls rls_platform_session
-- (PLATFORM / Super-Admin path). Read-side it serves the console; out-of-band the
-- A1 generator consumes the frozen IR.
--
-- ----------------------------------------------------------------------------
-- RLS: NOT enabled (platform-scoped, the identity_mirror precedent, D36/D104)
-- ----------------------------------------------------------------------------
-- This is a PLATFORM-scoped table: an Atlas draft is cross-tenant, Super-Admin
-- state, not tenant-private data. It has no tenant_id and no RLS policy. RLS would
-- add no isolation that matters and would block the platform write path. Because
-- the table carries no policy, a PLATFORM session (which "writes nothing" only
-- because of the tenant-scoped WITH CHECK on RLS tables) can write it.
--
-- ----------------------------------------------------------------------------
-- Ledger boundary (ADR-ATLAS-001 decision 6 / A5)
-- ----------------------------------------------------------------------------
-- This table is the ARTIFACT store (the frozen IR + status + version). The publish
-- AUDIT/ACTION ledger (who published what, when) is Customer Master's, A5. The
-- published_at / created_by_user_id columns are OPERATIONAL METADATA, not the
-- governance ledger; nothing may present them as the audit record.
--
-- ----------------------------------------------------------------------------
-- Dependencies
-- ----------------------------------------------------------------------------
--   - schema: atlas
--   - function: uuidv7()
-- ============================================================================


CREATE TABLE atlas.schema_drafts (

    id                  UUID            NOT NULL DEFAULT uuidv7(),
    vertical            VARCHAR(64)     NOT NULL,
    table_key           VARCHAR(128)    NOT NULL,

    -- Lifecycle. 'superseded' is RESERVED for A6 (evolution/diff); PR2 only ever
    -- sets 'draft' (on create) and 'published' (on freeze). It is in the CHECK so
    -- A6 needs no migration, but no PR2 code path writes it.
    status              VARCHAR(16)     NOT NULL,
    schema_version      INT             NOT NULL,

    -- The IR document: schema_to_document(SchemaIR). Round-tripped via parse_ir.
    ir                  JSONB           NOT NULL,

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Operational metadata (NOT the CM publish ledger; see the boundary note above).
    published_at        TIMESTAMPTZ     NULL,
    created_by_user_id  UUID            NULL,

    CONSTRAINT pk_schema_drafts PRIMARY KEY (id),
    CONSTRAINT ck_schema_drafts_status
        CHECK (status IN ('draft', 'published', 'superseded'))
);


-- One frozen artifact per (vertical, table_key, schema_version): a duplicate
-- PUBLISHED version conflicts. Drafts are unconstrained on version (still 1 until
-- frozen), so the partial predicate scopes the uniqueness to published rows.
CREATE UNIQUE INDEX uq_schema_drafts_published_version
    ON atlas.schema_drafts (vertical, table_key, schema_version)
    WHERE status = 'published';


-- ----------------------------------------------------------------------------
-- MANDATORY immutability: a published row can never be UPDATEd, regardless of
-- code path. The WHEN keys on OLD.status='published', so it NEVER blocks the
-- legitimate draft->published transition (freeze updates a row whose OLD status
-- is 'draft'); it only rejects mutating an already-published row.
-- ----------------------------------------------------------------------------
CREATE FUNCTION atlas.reject_published_update()
RETURNS TRIGGER AS $$
BEGIN
    -- Build the message by string concatenation rather than a RAISE format argument:
    -- this DDL is applied through psycopg, which reads a lone percent sign as a bind
    -- placeholder, so the message text must contain none.
    RAISE EXCEPTION USING
        MESSAGE = 'atlas.schema_drafts row ' || OLD.id || ' is published and immutable',
        ERRCODE = 'raise_exception';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_schema_drafts_immutable_published
    BEFORE UPDATE ON atlas.schema_drafts
    FOR EACH ROW
    WHEN (OLD.status = 'published')
    EXECUTE FUNCTION atlas.reject_published_update();
