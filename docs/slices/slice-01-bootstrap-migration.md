# Slice 1: Bootstrap Alembic migration

## Depends on

Phase 0 for the DDL files under `schemas/postgres/` (source of truth for
schema definition) and the two bootstrapped Postgres roles `ithina_dis_admin`
(migrations) and `ithina_dis_user` (NOSUPERUSER NOBYPASSRLS, service code).
No prior slice.

## Goal

After this slice, applying `alembic upgrade head` against an empty Postgres
database produces a complete, RLS-protected, partitioned DIS schema set ready
for service code to read and write. The same migration is environment-portable:
it runs identically against local Postgres (port 5433) and against Cloud SQL
once provisioned.

## Task

Create a single Alembic migration `alembic/versions/0001_bootstrap.py` that
applies every DDL file in `schemas/postgres/` in dependency order, producing:

- 7 schemas: `canonical`, `bronze`, `config`, `identity_mirror`,
  `quarantine`, `staging`, `audit`, plus the `uuidv7` extension function.
- All tables defined by the DDL files, with their declared constraints, RLS
  posture, partitioning, and indexes.
- For each partitioned parent table: 7 initial daily partitions covering
  `CURRENT_DATE - 1` through `CURRENT_DATE + 5`.
- All grants on `ithina_dis_user` as declared by the DDL files.

The migration is a manifest: it lists DDL files in dependency order and executes
each via `op.execute(Path(path).read_text())`. SQL files remain the source of
truth for schema definition. The migration does not hand-author DDL.

## Acceptance criteria

1. `alembic upgrade head` succeeds on a fresh local Postgres.
2. `\dn` in psql lists exactly the 7 expected schemas plus `public`.
3. `\dt canonical.*` shows all canonical tables; same for every other schema.
4. `make check` still reports 57/57 PASS; no tier regresses. (Whether a tier
   should additionally assert post-migration schema presence is an open
   question below.)
5. RLS smoke test: connecting as `ithina_dis_user` without
   `SET LOCAL app.tenant_id` returns 0 rows from any RLS-protected,
   tenant-scoped table. (Posture per CLAUDE.md hard rule 1.)
6. Partition smoke test: an INSERT with `event_date = CURRENT_DATE` into any
   partitioned event table succeeds with no "no partition found" error.
7. `alembic downgrade base` cleanly removes everything (schemas, tables,
   extension).
8. Re-running `alembic upgrade head` after downgrade reaches the same end state.

## Scope boundary

In scope:
- Apply existing DDL files as-is via `op.execute()`.
- Create 7 daily partitions per partitioned parent table using `CURRENT_DATE`
  at migration-run time. Partition CREATE statements use
  `CREATE TABLE IF NOT EXISTS` for cloud-replay safety.
- Verify the migration is idempotent (re-running has no effect).
- Verify the migration is environment-portable (no hardcoded host, port, or
  role names; reads `POSTGRES_ADMIN_URL` from env).

Out of scope:
- Authoring new DDL. If a DDL file is missing or wrong, surface it in plan mode
  rather than fix it in this slice.
- Seeding any data (no test tenants, mappings, or users). That is Slice 2.
- Partition-management automation (scheduled creation of future partitions).
  Deferred to a Phase 3 infrastructure slice.
- Cloud SQL provisioning. This slice produces a migration that will run
  identically against Cloud SQL once that exists.
- Any service code, lib code, or test infrastructure.

## Constraints

- One migration file: `alembic/versions/0001_bootstrap.py`.
- Migration is a manifest, not hand-authored DDL. DDL files are the source of
  truth; the migration applies them verbatim.
- Apply DDL files in dependency order. Suggested order (confirm in plan mode by
  examining FK declarations):
    1. `00_extensions/uuidv7_setup.sql`
    2. `identity_mirror/tenants.sql`
    3. `identity_mirror/stores.sql`
    4. `config/source_mappings.sql`
    5. `bronze/data_ingress_events.sql`
    6. `canonical/*.sql` (4 files; order per FKs)
    7. `staging/*.sql` (4 files)
    8. `quarantine/*.sql` (2 files)
    9. `audit/events.sql`
- Partition initialization uses `CURRENT_DATE` at migration-run time, not a
  hardcoded date.
- All partition CREATE statements use `IF NOT EXISTS`.
- `mapping_version_id` columns on canonical event tables FK to
  `config.source_mappings(mapping_version_id)` with `ON DELETE RESTRICT` (mapping
  version pinning, CLAUDE.md hard rule 5, decisions.md D22).
- RLS posture per existing DDL: tables with RLS = `bronze.*`, `canonical.*`,
  `quarantine.*`, `audit.events`, `staging.*`. Tables without RLS =
  `identity_mirror.tenants`, `identity_mirror.stores`.
  `config.source_mappings` posture to be confirmed in plan mode.
- Event tables carry no UNIQUE constraint (append-only, CLAUDE.md hard rule 7,
  decisions.md D33). The migration must not add one.

## Open questions (for plan mode to resolve)

1. Confirm `schemas/postgres/audit/events.sql` exists. The Phase 0 build-guide
   line lists DDL for canonical, bronze, config, identity_mirror, quarantine,
   staging, and the UUIDv7 extension, but does not name `audit`. The Slice 1
   build-guide line and architecture both reference `audit.events`. If the
   audit DDL is absent, flag it; do not author it (out of scope).
2. Verify each DDL file's RLS posture matches the expectation above.
   Specifically: does `config.source_mappings.sql` declare RLS or not?
3. Verify every canonical event table's DDL already declares the
   `mapping_version_id` FK with `ON DELETE RESTRICT`. If not, flag the gap; do
   not silently add.
4. Verify the suggested DDL-application order against actual FK declarations.
   Identify any cycle or missing dependency.
5. Identify which tables are partitioned and confirm initial partition creation
   for each. Suggested partitioned tables (per architecture): canonical event
   tables, `signal_history`, `audit.events`. Confirm.
6. Determine the correct `downgrade()` implementation. Symmetric to `upgrade()`
   (drop in reverse order), or a single `DROP SCHEMA ... CASCADE` per schema?
   Consider re-run safety (acceptance 7 and 8).
7. Confirm what `make check` asserts today and whether the migration changes the
   count. If no current tier verifies schema presence post-migration, decide
   whether one should be added or whether acceptance 4 stands as written.
