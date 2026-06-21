# services/dis-ui-server — Claude Code Context

Loaded when Claude Code works in `services/dis-ui-server/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

The BFF (backend-for-frontend) for the DIS UI. Single backend service hosting all UI-facing sub-modules: sample upload, onboarding review, mapping CRUD, quarantine console, audit lookup, DuckDB query panel, and the synchronous csv-uploads endpoint — CSV upload Phase 1 (placement per `decisions.md` D36; the signed-URL mechanic is SUPERSEDED by Slice 8's stream-through design). Hosts the onboarding sub-module in-process.

For the EPE block (purpose, entry, process, exit), file structure, and operational detail, see `README.md` in this directory. For the current build slice, see the slice doc in `docs/slices/`.

**Status:** v1.0.

## Rules specific to this service

- Writes to: `config.source_mappings` (mapping authoring); the bronze GCS bucket (the Slice 8 CSV-upload object, canonical D53 path via `dis-storage` only); `audit.events` via `dis-audit` (fire-and-forget); Pub/Sub `csv.received` (the Slice 8 upload trigger, D54), `mapping.changed` (notify streaming consumer on active mapping change), and `ingress.resubmit` (resubmit from quarantine console). Do not write to other tables or topics from here. NEVER bronze tables (the worker owns bronze).
- Reads from: Cloud SQL read replica (canonical), Cloud SQL `audit.events` (Phase 1; BigQuery `audit_events` from Phase 3 onward per D34), `config.source_mappings`, `quarantine.*`, `identity_mirror` (via identity-service).
- All Postgres access uses `libs/dis-rls`. No raw SQLAlchemy sessions.
- Audit reads from Cloud SQL `audit.events` in Phase 1 via standard repos. BigQuery via `libs/dis-core` BqClient lands in Phase 3 (BqClient is a stub in Phase 1).
- All audit emission uses `libs/dis-audit`.
- Never writes to canonical tables or audit. Never publishes to `ingress.ready` or `quarantine` (those are receiver/worker concerns).
- Authenticates via Customer Master JWT; extracts tenant_id and role claims; FastAPI dependency injection scopes every request.
- Hosts the `csv_uploads` handler (Slice 8): the file streams THROUGH this service to GCS in one request — no signed PUT URL, no upload-session object, no completion detection (supersedes D36's mechanic; closes D54's fork). Mints `trace_id` here (the receiver, hard rule 4); the worker reads it off the `csv.received` EVENT (D54), never from the object path. The 10 MB cap is enforced MID-STREAM (`upload_stream.py` is the reusable pattern for any later file-body endpoint). `template_id` is validated ACTIVE and carried end to end but the streaming consumer stays template-unaware until Slice 8a (D71 — no promote-to-ACTIVE path may ship before 8a).
- Onboarding sub-module is in-process; not a separate service. See architecture §4.16, §4.17.
- DuckDB query panel is ops-role-restricted. RBAC enforced at the handler level.

## Durable invariants (established in Slice 13a)

- **`/api/v1` prefix.** Every UI data endpoint mounts under the `/api/v1` prefix via
  `api.py`'s `api_router` (the prefix constant is `config.API_PREFIX`); the contract's
  relative `/v1/<group>/<resource>` paths are unchanged — only the deployed base is
  `/api/v1`. Health probes (`/healthz`, `/readyz`) stay at the ROOT, per infra
  convention. dis-ui's `client.ts` fetch base must agree (`/api/v1`) when the
  frontend's real mode wires up (13b/19, API_CONTRACT Appendix B).
- **ORM through `dis-rls` only.** This service uses the SQLAlchemy ORM/declarative
  layer (`db.py` `Base`; D67). Any model on that base executes ONLY inside
  `rls_session(engine, tenant_id)` — never a raw `AsyncSession`, never a second
  engine. The engine comes from `create_rls_engine` so the
  `current_database()=='ithina_dis_db'` + NOBYPASSRLS guard covers every
  connection.

## Durable invariant (established in Slice 14b)

- **Declarative models execute CORE-STYLE on the `rls_session` connection** —
  `await conn.execute(select(Model)…/insert(Model)…/update(Model)…)` — never via an
  `AsyncSession` bound to it. Reason: `rls_session` owns the GUC-scoped transaction
  (`SET LOCAL app.tenant_id`); a `session.commit()` inside the block commits that
  transaction EARLY, and every subsequent statement on the connection autobegins a
  NEW transaction with no `app.tenant_id` — under RLS fail-closed policies it reads
  zero rows and writes nothing, SILENTLY. Core-style execution has no commit to
  miscall, no identity map, no autoflush, and is fully typed via `Mapped[...]`.
  This is the reusable data-access pattern for every later API slice; repos
  (`repos/`) are the only modules that build statements against the ORM models.
- **The auth seam is the sole source of `tenant_id`.** `auth/scope.py` dependencies read
  the verified token only — never a body, query param, or unverified header — and are the
  only path by which a tenant scope reaches a session. Sole exception (Slice 17b, D92): a
  PLATFORM impersonation write reads its acted-for tenant from the request body, honoured
  ONLY on a verified `user_type=PLATFORM` token (a TENANT naming one is rejected 403).
  `auth/verifier.py` is the single swap point for the 13b JWKS verifier.
- **DIS-side RLS is two-GUC** (`app.user_type` + `app.tenant_id`, Slice 17b / D91): TENANT
  scopes to its tenant; PLATFORM (with `dis:ops`) reads all tenants and writes only an
  impersonated tenant. The asymmetry is structural — the PLATFORM branch is in USING only,
  never WITH CHECK. The CM-replica two-GUC pattern
  (`docs/ithina_master_db_read_access.md`) is a separate, Customer-Master-only path.

## References

- `README.md` (this directory) — EPE block, file structure, behavioural detail.
- Root `CLAUDE.md` — project-wide invariants.
- `docs/architecture.md` §4.17 — dis-ui-server module rationale; §4.16 onboarding sub-module.
- `docs/decisions.md` D17 — single BFF; D26 — BFF rationale; D34 — Phase 1 audit destination; D36 — Phase 1 upload-session endpoint lives here.

## When uncertain

Refer to the current slice doc in `docs/slices/`. If silent there, refer to architecture. If still uncertain, ask before coding.
