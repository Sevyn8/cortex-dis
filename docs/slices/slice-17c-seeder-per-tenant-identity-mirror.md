# Slice 17c: seeder writes identity_mirror per-tenant (GUC pattern, RLS-off-safe)

This slice rewrites the test seeder so its identity_mirror writes follow the system's
tenant-scoped write pattern: one session per tenant, both GUCs set (`app.user_type='TENANT'`
+ `app.tenant_id=<that tenant>`), exactly as the production mirror-sync job writes. Today the
seeder writes all tenants' identity_mirror rows in one GUC-less bulk session, an exception to
the pattern that only works because identity_mirror is RLS-OFF. This slice removes that
exception. It is test-infrastructure only: no production code, no DDL, no migration, and
identity_mirror stays RLS-OFF throughout. It is a standalone prep step; it does NOT require
any follow-up slice to be correct or to leave the system working. (A later, separate slice
may bring identity_mirror under RLS; this slice neither performs nor presumes it.)

## Depends on

- The seeder, verified: `libs/dis-testing/src/dis_testing/seed.py` writes three things in ONE
  shared transaction (`eng.begin()`): identity_mirror.tenants (loop over all tenants),
  identity_mirror.stores (loop over all stores), then config.source_mappings. The
  identity_mirror writes set NO GUC (the module docstring states "the identity_mirror writes
  stay GUC-independent"); config.source_mappings already sets its own `app.tenant_id`
  (RLS-ON since Slice 14a). Inserts are `ON CONFLICT DO NOTHING` (idempotent).
- The production reference shape, verified: `services/mirror-sync-consumer/.../sinks/
  postgres.py` upserts identity_mirror per-tenant, "one transaction per tenant," via
  `rls_session(tenant)` (which sets both `app.user_type='TENANT'` and `app.tenant_id`),
  writing only that tenant's rows per session. The seeder is sync (`create_engine`,
  `Connection`); production is async (`AsyncEngine`, `rls_session`), so the seeder matches the
  shape by setting the GUCs by hand on the sync connection, not by calling `rls_session`.
- The seeder's role posture, verified: most callers run the seeder as `ithina_dis_user`
  (NOBYPASSRLS); two migration-test callers (test_migration_0009, test_migration_0011) run it
  as `ithina_dis_admin` (BYPASSRLS). A GUC set under a BYPASSRLS session is simply ignored, so
  the per-tenant rewrite is correct under both roles (no-op-or-honoured, never wrong).
- The behavioural contract, verified (`libs/dis-testing/tests/integration/test_seed.py`):
  idempotency (second run inserts 0), the `SeedSummary` counts, and all fixture tenants/stores
  present (`count >= len(fx.TENANTS)`, `>= len(fx.STORES)`). No caller relies on the three
  tables committing in a single transaction.
- Decisions honoured: D41 (identity_mirror RLS-OFF stays, untouched here; the per-tenant GUC
  is "a harmless no-op under RLS-off," exactly as D41 records for the production job), the
  mechanism-not-policy rule, `services/dis-ui` read-only.

## Goal

After this slice, the seeder writes identity_mirror.tenants and identity_mirror.stores
per-tenant, each in its own session with both GUCs set (`app.user_type='TENANT'` +
`app.tenant_id=<that tenant>`), matching the production mirror-sync write shape. The seeder no
longer writes identity_mirror in a GUC-less bulk session. The system works identically with
RLS OFF (the GUCs are a no-op against the RLS-OFF identity_mirror tables), proving the change
stands alone and presumes no follow-up. The seeder's behavioural contract (idempotency,
`SeedSummary` counts, all fixture rows present) is unchanged.

## The change (fixed; CC designs the exact code, shows it for review)

- The identity_mirror writes (the tenants loop and the stores loop) move from one shared
  GUC-less `eng.begin()` transaction into per-tenant sessions: for each tenant, open a
  session/transaction, set both GUCs (`app.user_type='TENANT'`, `app.tenant_id=<that
  tenant>`), and write that tenant's tenant-row and that tenant's stores only. This follows
  the system's tenant-scoped write pattern in full (both GUCs), not a minimal-tenant_id-only
  variant.
- config.source_mappings (step 3) keeps its existing GUC-scoped write, undisturbed. It is
  single-tenant and already sets its own `app.tenant_id`; the rewrite must not change its
  behaviour. (The current single-shared-transaction across all three tables is not a contract
  anything relies on; splitting identity_mirror into per-tenant sessions is allowed.)
- The GUCs are set transaction-locally (the `set_config(name, value, true)` form the codebase
  uses), by hand on the sync connection (the seeder is sync; it does not call the async
  `rls_session`).
- PLATFORM does not enter the seeder. The seeder establishes tenants' own data, which is
  always a TENANT-shaped write. (A PLATFORM write would write nothing under a future
  tenant-pinned WITH CHECK anyway; PLATFORM belongs to a future RLS-ON slice's tokens and
  read-tests, never to seeding.)

## Scope boundary

In scope:
- `libs/dis-testing/src/dis_testing/seed.py`: rewrite the identity_mirror tenants/stores
  writes to per-tenant sessions with both GUCs set; leave the config.source_mappings write's
  behaviour unchanged.
- Any seeder test adjustment needed to keep `libs/dis-testing/tests/integration/test_seed.py`
  green (the contract: idempotency, counts, all fixture rows).

Out of scope (and must not be touched):
- Any RLS policy, DDL, or migration. identity_mirror stays RLS-OFF in this slice. Bringing it
  under RLS is a separate, later slice that this one neither performs nor requires.
- Any production code: the mirror-sync-consumer (already per-tenant), the streaming/dis-ui
  readers, repos/stores.py and its D70 in-query predicate (untouched here; D70 is the RLS-ON
  slice's concern).
- `services/dis-ui` (frozen frontend), excluded from all tooling.
- The config.source_mappings write's tenant scoping (it already sets its GUC; do not change
  what it does, only ensure the identity_mirror restructure does not disturb it).

## Constraints

- **Follow the GUC pattern, do not cut corners.** The seeder sets BOTH GUCs
  (`app.user_type='TENANT'` + `app.tenant_id`) per tenant, matching every other tenant-scoped
  writer, not a minimal `app.tenant_id`-only shape that merely happens to be sufficient.
- **RLS-off-safe / stands alone.** The change must leave the system fully working with RLS
  OFF; the GUCs are a no-op against the RLS-OFF identity_mirror tables. This slice must NOT
  make any follow-up RLS-ON slice mandatory, and must NOT half-apply RLS.
- **Preserve the seeder contract.** Idempotency (`ON CONFLICT DO NOTHING`, second run inserts
  0), the `SeedSummary` counts, and all fixture tenants/stores present. Every fixture tenant
  and store must still be seeded (the per-tenant loop covers all tenants).
- **Correct under both roles.** The rewrite must work whether the seeder runs as
  `ithina_dis_user` (NOBYPASSRLS) or `ithina_dis_admin` (BYPASSRLS); a GUC under BYPASSRLS is
  ignored, which is fine.
- **Test-infrastructure only.** No production code, no DDL, no migration. `services/dis-ui`
  untouched.

## Open questions for plan mode (CC resolves against the live repo; ERROR, not skip)

1. The exact sync mechanism for per-tenant GUC setting in the seeder: how to open a
   transaction per tenant on the sync `Connection`/`Engine` and set both GUCs
   transaction-locally (the `set_config(..., true)` form), and whether to reuse one engine
   across the per-tenant transactions or open per-tenant. Confirm against the seeder's current
   sync structure.
2. How the config.source_mappings step (currently in the shared transaction, single-tenant,
   already GUC-scoped) is kept correct once the identity_mirror writes are split out: does it
   move into the matching tenant's session, run in its own session after the loop, or stay as
   it is? Pick the smallest change that preserves its current behaviour.
3. Whether the `SeedSummary` counting (tenants_inserted, stores_inserted, mappings_inserted)
   still aggregates correctly across per-tenant sessions, so test_seed.py's count assertions
   and idempotency check still pass unchanged.
4. Any caller or fixture that would be affected by the per-tenant restructure beyond the
   seeder's own tests (the plugin fixture, service conftests, repo-root integration tests):
   confirm none relies on single-transaction atomicity across the three tables, so the split
   is safe.

## Acceptance criteria

1. The seeder writes identity_mirror.tenants and identity_mirror.stores per-tenant, each in a
   session with BOTH GUCs set (`app.user_type='TENANT'` + `app.tenant_id=<that tenant>`); the
   GUC-less bulk write of identity_mirror is gone. Confirmed by reading the rewritten seeder.
2. **Stands alone with RLS OFF:** the full test suite passes with identity_mirror still
   RLS-OFF (no policy, no migration). This proves the change presumes no follow-up and does
   not half-apply RLS. (The acceptance gate for this slice.)
3. The seeder contract holds: idempotency (second run inserts 0), `SeedSummary` counts, and
   all fixture tenants/stores present, proven by `test_seed.py` green and unchanged in intent.
4. config.source_mappings is still seeded correctly (its tenant-scoped write behaviour
   unchanged); RLS-ON tests touching it do not regress.
5. The rewrite works under both seeder roles (NOBYPASSRLS `ithina_dis_user` and BYPASSRLS
   `ithina_dis_admin`): both the plugin/most callers and the two admin-path migration tests
   pass.
6. No production code, no DDL, no migration changed; `services/dis-ui` unmodified; no RLS
   policy added to identity_mirror.
7. `make check` / lint / mypy `--strict` clean (dis-ui excluded); the seeder change and any
   test adjustment ship in the same commit.
8. Full regression: the broader suites that seed identity_mirror FK targets (streaming-
   consumer, csv-ingest-worker integration; the repo-root migration/RLS tests) still pass,
   confirming the per-tenant seeder is a transparent substitute for the bulk seeder.
