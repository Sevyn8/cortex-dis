# services/mirror-sync-consumer — Claude Code Context

Loaded when Claude Code works in `services/mirror-sync-consumer/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

Maintains the `identity_mirror` schema in the data-platform Postgres so canonical tables can have real FKs. Two modes: DB-pull from Customer Master DB (v1.0 launch path) and Pub/Sub consumer on `identity.changed` (deferred until Customer Master emits).

For the EPE blocks (one per mode), file structure, and operational detail, see `README.md` in this directory. For the current build slice, see the slice doc in `docs/slices/`.

**Status:** v1.0 — DB-pull mode is the active path; Pub/Sub consumer mode is deferred.

## Rules specific to this service (Slice 7, DB-pull mode)

- **Two Postgres instances, two guards.** Reads Customer Master (`CM_DB_URL`); writes DIS
  (`POSTGRES_URL`). The CM read positively asserts `current_database()` is the expected CM
  database (and never `ithina_dis_db`); the DIS write goes through `dis-rls` `rls_session`,
  inheriting its `current_database()=='ithina_dis_db'` + NOBYPASSRLS guard. A target mix-up
  fails before any write; the entrypoint exits non-zero.
- Writes to: `identity_mirror.tenants`, `identity_mirror.stores`. Nothing else, no topics.
- The CM DB read is bounded to `pull/reader.py`. It does **not** use `dis-rls` (that helper
  refuses any non-`ithina_dis_db` database); it carries its own CM-specific read session.
- **CM read runs under the platform context**, set transaction-locally: `app.user_type='PLATFORM'`,
  `app.tenant_id=NULL`. Unset/mis-set context silently returns zero rows, so the run asserts the
  context took effect and fails loud before writing. An empty CM under a *confirmed* context is a
  valid first-load (log + exit 0), not a failure.
- **Upsert-only.** Insert + update on the natural keys (`pk_imt` / composite `pk_ims`); **never
  delete, never soft-delete.** Lifecycle is Customer Master's `status` replicated verbatim — there
  is **no `is_active` column** (the docs that name one are stale; see `decisions.md`). Conditional
  `DO UPDATE ... WHERE IS DISTINCT FROM EXCLUDED` keeps a no-change re-run a true no-op.
- **External codes copied as-is (Slice 9a, D55).** `tenants.display_code` and `stores.store_code`
  are replicated verbatim from Customer Master — both nullable (the source columns are), no
  transformation, no defaulting. They are readability-only, never a translation bridge (D37: the
  UUID is the identity). Backfill of pre-9a rows is the normal sync run, idempotent via the
  DISTINCT clauses — there is no one-off backfill step.
- **Audit is log-only this slice.** Run start/end + per-tenant counts are structured log lines; no
  `audit.events` rows, **no `dis-audit` dependency** (the audit vocabulary has no operational/
  non-ingress slot — registered gap, deferred; see `decisions.md`).
- **DB-pull mode only.** The Pub/Sub consumer (`identity.changed`) is deferred (D35) and not
  scaffolded; when built it will share the `sync/` upsert path but run as a long-lived listener.
- Errors are `dis-core` errors (`MirrorSyncError` / `CustomerMasterReadError`; the DIS write guard
  raises `RlsContextError`); never raw `RuntimeError`/`ValueError`. UUIDs only via `dis-core`.

## References

- `README.md` (this directory) — EPE block, file structure, behavioural detail.
- Root `CLAUDE.md` — project-wide invariants.
- `docs/architecture.md` §4 — module rationale.
- `docs/decisions.md` — indexed decision register.

## When uncertain

Refer to the current slice doc in `docs/slices/`. If silent there, refer to architecture. If still uncertain, ask before coding.
