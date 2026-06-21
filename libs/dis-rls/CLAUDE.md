# libs/dis-rls — Claude Code Context

Loaded when Claude Code works in `libs/dis-rls/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

The RLS-aware async Postgres session helper. Every canonical read/write goes through
`rls_session`, which opens a per-tenant scoped transaction so one tenant cannot read
another's rows (root hard rules 1 & 12).

For interfaces, types, file structure, see `README.md`.

## Rules specific to this lib (Slice 4)

- **Surface is minimal: `session.py` only.** `create_rls_engine(url=None)` factory +
  `rls_session(engine, tenant_id)` async context manager yielding an `AsyncConnection`.
  Do NOT build the README's speculative `batch.py` / `enforcement.py` until a real
  consumer needs them.
- **Caller owns the engine — no hidden global.** A process-wide async engine reused
  across event loops is a footgun; the caller creates the engine (app lifespan in
  services; loop-scoped fixture in tests) and passes it in.
- **Scope is set via `set_config(..., is_local=true)`** inside the transaction:
  `rls_session` sets `app.user_type='TENANT'` + `app.tenant_id`; `rls_platform_session`
  sets `app.user_type='PLATFORM'` with an empty (see-all) or impersonation `app.tenant_id`
  (parameterisable; `SET LOCAL` cannot bind a param). Commit on clean exit, roll back on
  exception.
- **Role posture is explicit, not assumed.** On first use of an engine the helper
  verifies (and refuses otherwise, raising `RlsContextError`) that the connection
  reached `ithina_dis_db` and the role is NOSUPERUSER / NOBYPASSRLS — RLS is silently
  void for a bypassing role. `current_database()` is the DIS-vs-Customer-Master
  discriminator; an `inet_server_port()` check is useless (docker reports 5432).
- **Never set `tenant_id` from a request body** — except a verified PLATFORM acted-for tenant (Slice 17b / D92). Otherwise derive it from authenticated context.
- Errors are `dis-core` `RlsContextError` (rooted in `DisError`); never raw
  `RuntimeError`/`ValueError`.

## References

- `README.md` — interface and structure.
- Root `CLAUDE.md` — project-wide invariants. `decisions.md` D41 (identity_mirror RLS, OPEN).
