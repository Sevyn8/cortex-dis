-- Runs once on first container start (fresh volume).
-- Creates the application role with production-correct posture:
--   NOSUPERUSER  → role is NOT a superuser
--   NOBYPASSRLS  → RLS policies apply to this role; queries that lack
--                  app.tenant_id context will return empty results, not
--                  silently leak cross-tenant data.
--
-- The bootstrap superuser is ithina_dis_admin (from docker-compose
-- POSTGRES_USER); used only for migrations.

CREATE ROLE ithina_dis_user
  WITH LOGIN
       NOSUPERUSER
       NOBYPASSRLS
       NOCREATEDB
       NOCREATEROLE
       PASSWORD 'ithina_dis_password';

-- Grant connect on the database
GRANT CONNECT ON DATABASE ithina_dis_db TO ithina_dis_user;

-- Grant usage on the default public schema (DIS schemas come from
-- Alembic migrations and will need their own GRANT statements).
GRANT USAGE ON SCHEMA public TO ithina_dis_user;
