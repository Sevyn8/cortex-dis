-- ============================================================================
-- DIS role bootstrap (CLOUD) — schemas/postgres/00_bootstrap/roles.sql
--
-- Creates the two DIS Postgres roles with production-correct posture:
--   - ithina_dis_admin : the migration role. Owns schemas/tables; runs Alembic.
--                        CREATEDB + CREATEROLE, but NOT a literal SUPERUSER —
--                        Cloud SQL forbids a true superuser, and the migration
--                        does not need one (it only creates schemas/objects and
--                        sets privileges, all of which CREATEDB/CREATEROLE +
--                        object ownership allow).
--   - ithina_dis_user  : the service-code role. NOSUPERUSER NOBYPASSRLS so RLS
--                        policies apply; queries without app.tenant_id return
--                        empty rather than leaking cross-tenant data. Mirrors
--                        infra/local/postgres-init.sql exactly.
--
-- ----------------------------------------------------------------------------
-- WHERE THIS RUNS — and where it does NOT
-- ----------------------------------------------------------------------------
-- This file is for CLOUD bootstrap (Cloud SQL) only. It is NOT part of the
-- Alembic migration and the migration never executes it.
--
-- LOCAL dev does NOT use this file: the docker-compose Postgres container
-- creates ithina_dis_admin as its POSTGRES_USER, and
-- infra/local/postgres-init.sql creates ithina_dis_user on first start.
--
-- CLOUD run-order (run ONCE per fresh Cloud SQL instance, BEFORE migrating):
--   1. Connect to the target DIS database as the Cloud SQL admin
--      (e.g. the `postgres` / cloudsqlsuperuser account).
--   2. Run THIS file, supplying passwords as psql variables (never hardcoded):
--        psql "<admin connection to the DIS database>" \
--             -v dis_admin_password="$DIS_ADMIN_PASSWORD" \
--             -v dis_user_password="$DIS_USER_PASSWORD" \
--             -f schemas/postgres/00_bootstrap/roles.sql
--   3. Export POSTGRES_ADMIN_URL to point at the DIS database as ithina_dis_admin.
--   4. Run `alembic upgrade head` (creates schemas, objects, grants, partitions).
-- (This order is also recorded in docs/build-guide.md under Slice 1.)
--
-- ----------------------------------------------------------------------------
-- Idempotency
-- ----------------------------------------------------------------------------
-- Each role is created only if absent (WHERE NOT EXISTS ... \gexec). Re-running
-- is safe and a no-op for existing roles. Passwords are NOT rotated on re-run;
-- rotate explicitly with ALTER ROLE if needed.
-- ============================================================================


-- ---------- ithina_dis_admin (migration role) ----------
-- Created only if absent. CREATEDB + CREATEROLE; explicitly NOSUPERUSER and
-- NOBYPASSRLS (admin is never in the tenant data path).
SELECT 'CREATE ROLE ithina_dis_admin'
       || ' WITH LOGIN NOSUPERUSER NOBYPASSRLS CREATEDB CREATEROLE'
       || ' PASSWORD ' || quote_literal(:'dis_admin_password')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'ithina_dis_admin'
)
\gexec


-- ---------- ithina_dis_user (service-code role) ----------
-- Matches infra/local/postgres-init.sql: LOGIN, NOSUPERUSER, NOBYPASSRLS,
-- NOCREATEDB, NOCREATEROLE.
SELECT 'CREATE ROLE ithina_dis_user'
       || ' WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE'
       || ' PASSWORD ' || quote_literal(:'dis_user_password')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'ithina_dis_user'
)
\gexec


-- ---------- Baseline grants for the service role ----------
-- CONNECT on the database this file is run against (portable across env names),
-- and USAGE on public. DIS schema/table/sequence grants are applied by the
-- bootstrap migration, not here.
SELECT 'GRANT CONNECT ON DATABASE ' || quote_ident(current_database())
       || ' TO ithina_dis_user'
\gexec

GRANT USAGE ON SCHEMA public TO ithina_dis_user;
