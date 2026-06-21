-- ============================================================================
-- Customer Master DB: create the read-only role used by DIS Mirror Sync (DB-pull mode).
--
-- This script runs identically on:
--   - Local Postgres (Customer Master devbox, port 5432).
--   - Cloud SQL (Customer Master cloud instance).
--
-- Purpose. Give DIS a dedicated, least-privilege role to read core.tenants and
-- core.stores from Customer Master. The role respects the RLS contract: it has
-- NOBYPASSRLS, so reads must run inside a transaction with the right GUCs set
-- (app.user_type='PLATFORM', app.tenant_id=NULL) per
-- docs/ithina_master_db_read_access.md.
--
-- The role is read-only. It cannot write any table in Customer Master.
--
-- Run as a Customer Master superuser:
--   psql "<admin connection string>" -f create-dis-mirror-reader.sql
--
-- ----------------------------------------------------------------------------
-- Idempotency
-- ----------------------------------------------------------------------------
-- All statements are guarded with IF NOT EXISTS / DO blocks. Re-running is safe.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Create the role
-- ----------------------------------------------------------------------------
-- LOCAL DEV: password set here for convenience.
-- CLOUD SQL: do NOT keep this password; rotate immediately, or use Cloud SQL
--            IAM authentication (recommended) and drop the password entirely.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dis_mirror_reader') THEN
        CREATE ROLE dis_mirror_reader
            WITH LOGIN
                 NOSUPERUSER
                 NOBYPASSRLS
                 NOCREATEDB
                 NOCREATEROLE
                 NOREPLICATION
                 PASSWORD 'dev-only-rotate-in-cloud';
    ELSE
        -- Confirm posture matches expectations on re-run.
        ALTER ROLE dis_mirror_reader
            WITH LOGIN
                 NOSUPERUSER
                 NOBYPASSRLS
                 NOCREATEDB
                 NOCREATEROLE
                 NOREPLICATION;
    END IF;
END
$$;


-- ----------------------------------------------------------------------------
-- 2. Grants: schema + tables
-- ----------------------------------------------------------------------------
-- USAGE on core schema, SELECT on the two tables DIS needs.
-- No grants on any other table in core or any other schema.

GRANT USAGE ON SCHEMA core TO dis_mirror_reader;

GRANT SELECT ON core.tenants TO dis_mirror_reader;
GRANT SELECT ON core.stores  TO dis_mirror_reader;


-- ----------------------------------------------------------------------------
-- 3. Defensive revokes
-- ----------------------------------------------------------------------------
-- Revoke any default PUBLIC privileges that could grant unintended access.

REVOKE ALL ON ALL TABLES    IN SCHEMA core FROM dis_mirror_reader;
GRANT  SELECT ON core.tenants TO dis_mirror_reader;
GRANT  SELECT ON core.stores  TO dis_mirror_reader;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA core FROM dis_mirror_reader;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core FROM dis_mirror_reader;


-- ----------------------------------------------------------------------------
-- 4. Verify
-- ----------------------------------------------------------------------------
-- After running this script, you can verify:
--
--   \du+ dis_mirror_reader
--     -> attributes should NOT include "Superuser" or "Bypass RLS".
--
--   SELECT grantee, table_schema, table_name, privilege_type
--     FROM information_schema.role_table_grants
--     WHERE grantee = 'dis_mirror_reader';
--     -> should show exactly two rows: SELECT on core.tenants and core.stores.
