# Slice 13a: dis-ui-server foundation (dis-ui-server)

## Depends on

- Slice 1 (bootstrap migration), applied: the DIS database, its schemas, the
  `ithina_dis_user` role (`NOSUPERUSER NOBYPASSRLS`), the centralized grants, and the
  production-matching RLS posture. **13a assumes this and creates none of it.** It writes
  no DDL and creates or alters no role, schema, grant, or policy. The posture is verified
  in plan mode (Task 0) and the plan RAISES if it is absent or wrong, rather than 13a
  building it (that is migration territory, not this service).
- Slice 3 (`dis-core`), built: `errors` (the `DisError` root, leaf-level), `ids`
  (`new_uuid7`), `trace_id` (mint + contextvar), `timestamps`, and structured `logging`.
  13a consumes these; the error-to-HTTP exception handlers map `DisError` leaves to the
  error envelope.
- Slice 4 (`dis-rls`), built: the async RLS-aware session context manager
  (`rls_session(engine, tenant_id)`) that opens a transaction, scopes it to the tenant,
  and runs every statement under it. 13a WIRES and CONSUMES this; it does not modify it.
  The platform-scoped (cross-tenant / ops) session variant is NOT built here, see Scope.
- Decisions this slice must honour: D26 (single BFF; 13a is its deployable base), D36
  (CSV-upload Phase 1 is a handler on this service, landing later in Slice 8), D25 + D56
  (auth is parked; the real Customer Master verifier and the JWT claim shape are unsigned,
  so 13a stands up a dev-stub verifier behind a single seam and the real JWKS swap is
  13b), D28 (the identity-service real + stale-while-error fallback is 13b, not here).
- CLAUDE.md hard rule 1: every DIS-database read or write runs through `dis-rls`, never
  raw SQLAlchemy; CI lint forbids the bypass. The ORM introduced here (see Goal) obeys
  this: it executes only through the `dis-rls` session, not a separate engine.
- New decision to REGISTER (operator call, not 13a's to settle): dis-ui-server uses the
  SQLAlchemy ORM / declarative layer where other DIS services use Core/text, justified by
  this service's CRUD and system-of-record nature (`config.source_mappings`). The
  load-bearing constraint is that the ORM runs through the `dis-rls` session (hard rule
  1); the layer choice itself wants a D-number so it is not later "corrected" to the house
  style. Flagged here; the operator assigns the number at the commit gate.
- Downstream: Slice 13b (identity-service real + JWKS auth) replaces the dev-stub verifier
  and the identity stub seam. Slice 8 (upload Phase 1) and Slice 14 (onboarding / mapping)
  hang their handlers off this base and introduce the first domain endpoints, the first
  `config.source_mappings` writes, and the first Pub/Sub publishes. 13a builds none of
  those.

## Goal

After this slice, `dis-ui-server` is a deployable FastAPI service that builds, runs as a
container, and stands on a foundation that makes cross-tenant leakage structurally
impossible from day one.

It answers two probes: `GET /healthz` is DB-free liveness returning `{"status": "ok"}`
(contract §2.7); `GET /readyz` is readiness that opens a tenant-scoped `dis-rls` session
and proves it works (200 when the RLS path is live, 503 when it is not). Readiness is the
day-one proof that the isolation path actually functions, not merely that Postgres
answered.

The auth seam lands as a single FastAPI dependency (`get_current_identity`, with
`require_tenant` / `require_ops` variants built on it) backed by a dev-stub verifier. Two
user types use dis-ui, both resolved from the verified token, never from anywhere else:

- **TENANT** user (a tenant operator): carries a `tenant_id`; sees only rows belonging to
  that tenant. The session is scoped to `user_type=TENANT` + that tenant UUID.
- **PLATFORM** user (a platform operator): carries no `tenant_id`; sees rows across all
  tenants. The session is scoped to `user_type=PLATFORM` with no tenant UUID.

This maps to the contract's `Identity.tenant_id: str | None` (None = platform) and the
`dis:ops` role. Two RLS regimes must not be conflated:

- **DIS database (this service's own reads/writes), single-GUC.** DIS-side RLS policies
  scope on `app.tenant_id` alone; there is no `user_type` / platform discriminator and no
  see-all-tenants posture in the DIS policies today (verify against the live policies in
  plan mode). A TENANT user's session sets `app.tenant_id` to its UUID and sees only its
  rows. A PLATFORM see-all read does NOT exist on the DIS side yet: enabling it requires
  both a `dis-rls` session variant AND a DIS policy migration introducing a platform
  discriminator. That migration and variant are out of 13a (see Scope) and belong to the
  first slice with a real cross-tenant ops read.
- **Customer Master replica (a different database, not touched in 13a), two-GUC.**
  `docs/ithina_master_db_read_access.md` describes the CM replica's `app.user_type` +
  `app.tenant_id` pattern (PLATFORM with `tenant_id` NULL sees all tenants). That pattern
  is CM-replica-only; it is the model the future DIS platform read may adopt, not the DIS
  posture as it stands. 13a reads no CM replica.

The auth seam is the SOLE source of the scoping inputs (`tenant_id`, and the
TENANT/PLATFORM distinction via `tenant_id` presence + role) that reach the session:
never a request body, query string, or unverified header, for either user type. That
single-source rule, plus the `NOBYPASSRLS` role and RLS on the tenant-scoped tables, is
the foundation rule (RLS on, no bypass, zero cross-tenant leakage). The dev-stub verifier
sits behind one seam so the real Customer Master JWKS verifier (13b / D25) replaces only
the verifier, leaving `Identity` and the dependencies stable.

13a builds the TENANT path end to end (it is what every 13a-and-near endpoint needs) and
proves the foundation rule on it. The PLATFORM path is seam-only here: `Identity` carries
`tenant_id=None` and `require_ops` gates it, but the DIS-side cross-tenant read (the
`dis-rls` platform session variant plus its enabling DIS policy migration) is NOT built in
13a. It lands in the first slice with a real cross-tenant ops read (ops fleet /
quarantine, a 15 to 17 placeholder); whether DIS-side RLS uses the same `user_type` GUC as
the CM replica or a different mechanism is a Task 0 derivation, not an assumption.

`dis-core` exception handlers map `DisError` leaves to HTTP status codes and the error
envelope (contract §2.3: `{error: {code, message, trace_id, details}}`). Structured
logging binds `service="dis-ui-server"` on every line, with `tenant_id` / `trace_id`
where in scope.

All UI data endpoints mount under an `/api/v1` prefix (the prefix mechanism is
established here so every later handler inherits it; the contract's relative
`/v1/<group>/<resource>` paths are unchanged, only the deployed base shifts). Health and
readiness probes stay at the root (`/healthz`, `/readyz`), per infra convention. This
prefix convention is written into `services/dis-ui-server/CLAUDE.md` as a durable
invariant during implementation, not restated per slice. The load-bearing requirement is
that the deployed base and dis-ui's `client.ts` fetch base agree (`/api/v1`) when the
frontend's real mode wires up (Slice 13b / 19, contract Appendix B); flag to the UI
engineer.

What it does NOT do: no UI data endpoints (mapping CRUD, onboarding, upload sessions,
quarantine, audit, ops, dashboard, notifications) are built here; they are Slices 8, 14,
and the 15 to 17 placeholders. No real Customer Master auth (JWKS swap is 13b). No
identity-service real implementation and no stale-while-error fallback (13b, D28). No
Customer Master replica reads (13b / Mirror Sync). No `dis-rls` platform-scoped
cross-tenant variant (the first slice that builds an ops cross-tenant read owns that; it
is a data-plane-lib edit and a guardrails-slice trigger). No DDL and no role / schema /
grant / policy creation (assumes Slice 1). No `config.source_mappings` write and no
Pub/Sub publish (Slice 8 / 14).

## Task

Build the service in the directory the repo reserves for it (`services/dis-ui-server/`);
confirm the actual scaffolding state in plan mode rather than assuming it (the service is
expected greenfield: only `CLAUDE.md`, `README.md`, and `API_CONTRACT.md` present, no
`src`, no tests). Decompose:

0. **Plan-mode grounding preconditions (ERROR, not skip).** Before proposing any code,
   derive the live state and RAISE in the plan if it is not as assumed:
   - **DB posture.** Confirm against the live DIS database that `ithina_dis_user` exists
     and is `NOSUPERUSER NOBYPASSRLS`, that the schema(s) this service's readiness probe
     and base wiring touch exist, and that RLS is enabled with a tenant policy on the
     tenant-scoped table the readiness probe will hit (see Task 4 / open question 2). If
     the role is missing or lacks `NOBYPASSRLS`, or the expected RLS policy is absent, the
     plan RAISES: 13a does not create or repair DB posture. Note D41 (`identity_mirror` is
     deliberately RLS-OFF, by live introspection) so CC does not flag that as a defect to
     fix.
   - **RLS scoping mechanism (TENANT vs PLATFORM).** Derive from the live DIS RLS policies
     HOW scoping is expressed: which GUC(s) the policy reads (`app.tenant_id` alone, or
     `app.tenant_id` + an `app.user_type`-style platform discriminator), and whether a
     platform / see-all-tenants posture exists on the DIS side at all. The CM replica
     pattern (`docs/ithina_master_db_read_access.md`) uses two GUCs (`app.user_type` +
     `app.tenant_id`); confirm whether DIS-side `dis-rls` mirrors that or uses a different
     mechanism, rather than assuming it copies CM. This grounds the deferred PLATFORM-scoped
     session variant (out of 13a) in the live policy. 13a's TENANT path uses whatever the
     live tenant policy requires; if `dis-rls` already exposes only a per-tenant
     `rls_session`, the PLATFORM variant is a registered `dis-rls` design point for the
     later ops-read slice, recorded here, not built.
   - **Lib surface.** Confirm `dis-rls` exposes the per-tenant `rls_session` and `dis-core`
     exposes the `errors` root, `ids`, `trace_id`, `timestamps`, and `logging` surface this
     slice consumes. If a needed symbol is absent, raise rather than invent it.
   - **Config / env.** Derive the env-var names and connection config shape from the
     existing services and the local stack (do not assert them here); `DIS_TARGET` routing
     and the Postgres connection follow the repo's established pattern.

1. **Service skeleton.** `main.py` (FastAPI app, router mounting), `config.py` (env-driven,
   per Task 0), `pyproject.toml`, `Dockerfile`, `.dockerignore`. Placement under
   `services/dis-ui-server/src/dis_ui_server/` per repo-structure; confirm the package
   layout in plan mode. Establish the `/api/v1` prefix mechanism for UI data endpoints
   (router prefix or `root_path`, decided in plan mode) with health probes at root; write
   the `/api/v1` convention into `services/dis-ui-server/CLAUDE.md` as a durable invariant.

2. **`dis-rls` wiring + ORM base.** Construct the async engine from config and expose the
   per-tenant `rls_session` to handlers. Introduce the SQLAlchemy declarative base for this
   service's later CRUD; in 13a there are no domain models yet (no endpoints), so this is
   the base plus the rule that any future model executes through the `dis-rls` session,
   never a raw session or a second engine (hard rule 1). Keep it minimal: the only live DB
   touch in 13a is the readiness probe.

3. **Auth seam.** The `Identity` value (`user_id`, `tenant_id | None`, `store_id | None`,
   `roles`), the `get_current_identity` dependency, and the `require_tenant` /
   `require_ops` variants (contract §2.1 / §2.2). `require_tenant` guarantees a `tenant_id`
   (TENANT user); `require_ops` guarantees the `dis:ops` role (PLATFORM user, `tenant_id`
   None). A dev-stub verifier validates a token and yields `Identity`; its parameters
   (algorithm, secret, issuer, audience) are taken from the contract §2.1 dev-stub
   definition so dev tokens round-trip with the UI's login stub, confirmed in plan mode
   against the contract rather than asserted here. The seam is defined by what its consumer
   needs (the `Identity` it yields and the guarantees of the `require_*` variants), NOT by
   the real verifier's internals, so the 13b JWKS swap touches only the verifier. Both
   `user_type` (TENANT/PLATFORM, derived from `tenant_id` presence + role) and `tenant_id`
   are read from the verified token only; the seam never yields a `tenant_id`-set /
   `user_type`-unset state.

4. **Health and readiness.** `GET /healthz`: unauthenticated, DB-free, `{"status": "ok"}`
   (contract §2.7). `GET /readyz`: opens a tenant-scoped `dis-rls` session and runs a
   trivial query inside it; 200 `{"status": "ready"}` when it succeeds, 503 (degraded)
   otherwise. Decide in plan mode whether readiness probes a real RLS-scoped table (the
   stronger foundation proof) or a neutral `SELECT 1` (open question 2); prefer the
   stronger proof if it does not require seeded data the local stack lacks.

5. **Error envelope + handlers.** FastAPI exception handlers map `DisError` leaves to
   status + the §2.3 envelope. The auth-seam error classes (`AuthTokenError` 401,
   `TenantScopeError` 403, `OpsRoleRequiredError` 403) are added to
   `libs/dis-core/errors.py` in the same commit as their use (contract §2.3), subclassing
   `DisError` with keyword-only context. This is an additive edit to a shared lib; note it
   in the plan so the blast radius is explicit.

6. **Structured logging.** `dis-core` logging bound with `service="dis-ui-server"`;
   `tenant_id` / `trace_id` on lines where they are in scope.

7. **Tests (same commit, code-quality rule 3).** The foundation rule is TEST-ENFORCED,
   not review-only:
   - `/healthz` returns `{"status": "ok"}` with no DB dependency (passes even with the DB
     down).
   - `/readyz` opens a tenant-scoped session and returns ready against the live local
     stack; returns 503 when the session cannot open.
   - The auth dependency: a valid dev-stub token yields an `Identity`; missing / expired /
     malformed yields 401 `AuthTokenError`; a tenant endpoint with no `tenant_id` yields
     403 `TenantScopeError`; `require_ops` without `dis:ops` yields 403
     `OpsRoleRequiredError`.
   - `tenant_id` cannot be sourced from a body, query, or unverified header: a test proves
     the token is the only path (the foundation rule, made executable).
   - A raised `DisError` leaf renders the §2.3 envelope with the correct status.

## Scope

**In:** the deployable skeleton; `dis-rls` per-tenant session wiring and the ORM base;
the auth-stub seam as the sole `tenant_id` source; `/healthz` + `/readyz`; the
`dis-core` error-to-HTTP envelope and the three new auth error classes; structured
logging; Dockerfile; the tests above.

**Out (with where each lands):** UI data endpoints (Slices 8, 14, 15 to 17); real
Customer Master JWKS auth (13b); identity-service real + D28 fallback (13b); CM-replica
reads (13b / Mirror Sync); the `dis-rls` platform-scoped cross-tenant session variant (the
PLATFORM-user see-all-tenants read; first ops-read slice; data-plane-lib edit, guardrails
trigger); any DDL or role / schema / policy work (assumes Slice 1); `config.source_mappings`
writes and Pub/Sub publishes (Slice 8 / 14).

## Open questions for plan mode

1. Config / env-var names and the connection config shape (derive from existing services).
2. Does `/readyz` probe a real RLS-scoped table or a neutral `SELECT 1`? Prefer the real
   table as the stronger foundation proof, unless it needs seeded data the local stack
   does not have at this slice.
3. ORM base placement, and whether any declarative model at all is warranted in 13a beyond
   the engine / session wiring (likely none, since there are no endpoints).
4. The dev-stub verifier parameters confirmed against contract §2.1 (not asserted in this
   doc).

## Acceptance criteria

- The service builds and runs as a container; `GET /healthz` returns 200 `{"status":
  "ok"}` with no DB dependency.
- `GET /readyz` opens a tenant-scoped `dis-rls` session and returns 200 `{"status":
  "ready"}` against the live local stack, 503 when the session is unavailable.
- The auth dependency behaves as the Task 7 tests specify; `tenant_id` is sourced only
  from the verified token, proven by test.
- A raised `DisError` leaf renders the §2.3 error envelope with the correct status.
- The dev-stub verifier sits behind one seam; the 13b JWKS swap requires no handler change
  (review-only: the seam shape).
- No DDL and no role / schema / grant / policy created or altered; the Task 0 precondition
  confirmed the DB posture, or the plan raised.
- The `/api/v1` prefix mechanism is established (health probes at root) and the convention
  is recorded in `services/dis-ui-server/CLAUDE.md`.
- `make check` / lint / mypy clean; tests ship in the same commit (code-quality rule 3).
