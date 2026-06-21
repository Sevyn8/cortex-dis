# Slice 17b: two-GUC platform/tenant RLS (PLATFORM see-all + tenant impersonation)

This slice is the realization of D76. It gives DIS a platform-user posture on top of
today's single-tenant isolation: an Ithina platform user reads across all tenants, and
(by naming one tenant per request) writes on that tenant's behalf. It is a load-bearing
security slice: the property it must never break is that a tenant user cannot read or
write another tenant's data. This doc is goal-level. The exact policy text, the dis-rls
entry-point shape, the auth-dependency change, and the endpoint wiring are CC's to design
in plan mode and show for review before any code. Because the slice is load-bearing, the
plan is a review artifact to iterate on, not a rubber stamp. The mechanism (the three
session modes, the asymmetric policy, the request-tenant-gated-on-PLATFORM rule) and the
constraints below are fixed; the how is CC's against the live repo.

## Depends on

- The single-GUC tenant-isolation posture, live and verified: 13 tenant-scoped tables
  (12 `tenant_isolation` policies plus `audit.events` `rls_audit_events_tenant`), all RLS
  ENABLED and FORCE, policy `tenant_id = current_setting('app.tenant_id', true)::uuid` in
  USING and WITH CHECK (the `audit.events` outlier is USING-only with an
  `OR tenant_id IS NULL` branch and no WITH CHECK). `identity_mirror.tenants` and
  `identity_mirror.stores` are RLS-OFF by design (D41) and out of this slice.
- `dis-rls` session helper, verified: `rls_session(engine, tenant_id)` sets a single GUC
  `app.tenant_id` via `set_config(..., is_local => true)`, and a first-use-per-engine
  guard `_check_posture` that RAISES `RlsContextError` if the connected database is not
  `ithina_dis_db` or the role can bypass RLS (`rolsuper`/`rolbypassrls`). Service code
  connects as `ithina_dis_user` (NOSUPERUSER, NOBYPASSRLS); `ithina_dis_admin` is
  Alembic-only.
- The migration convention, verified (D69 / D77 / D79 precedent): structural change edits
  the `schemas/postgres/*.sql` DDL file to the new end-state (the bootstrap migration
  `0001` re-reads those files at runtime, so a fresh build is born correct) AND adds a
  delta migration (DROP+CREATE, never ALTER POLICY; the inline copies in shipped migrations
  `0005`/`0007`/`0009` are frozen historical snapshots and are never edited), with a
  scratch-DB catalog-equality test proving fresh == migrated. Current head is `0010`; this
  slice appends `0011`.
- `dis-ui-server` auth seam, verified (Slice 13a): the dev-stub HS256 verifier, `Identity`
  built from the verified token only, `get_current_identity` / `require_tenant` /
  `require_ops` (`OPS_ROLE = "dis:ops"`), and `tenant_uuid_of`. Both `require_tenant` and
  `tenant_uuid_of` RAISE when `identity.tenant_id` is None, so they reject a
  PLATFORM-no-tenant identity today. The real Customer Master JWKS verifier is the deferred
  13b swap and is NOT wired here.
- The 27 `rls_session` call sites, verified: 11 JWT-driven `dis-ui-server` request sites
  (all tenant_id token-derived; 9 READ, 2 WRITE = POST/PATCH `/mapping-templates`), 15
  worker/lib sites (tenant_id from a Pub/Sub envelope or a pull loop, never a token), and 1
  unauthenticated health probe (synthetic UUID).
- Decisions honoured: D76 (this slice IS its realization), D41 (`identity_mirror` RLS-OFF,
  untouched), the fresh==migrated invariant, hard rule 1 (tenant isolation; this slice
  amends its wording, see the register decision below), the mechanism-not-policy rule, and
  the dis-ui-server "tenant_id is sole-sourced from the token" invariant (this slice opens
  a controlled, PLATFORM-only exception to it, see the register decision below).

## Decisions to REGISTER (operator assigns the numbers at the commit gate)

1. **Two-GUC posture supersedes the single-GUC wording of hard rule 1.** D76 scheduled
   this; the live contradiction is only with CLAUDE.md hard rule 1 stated as a standing
   single-GUC invariant. After this slice, tenant isolation is two-GUC (`app.user_type` +
   `app.tenant_id`); TENANT semantics are unchanged, PLATFORM is added. The CLAUDE.md
   wording (and its `SET LOCAL` vs `set_config` description) is reconciled at the gate.
2. **Request-supplied acted-for tenant, PLATFORM-only (controlled exception to
   token-sole-source).** Standard impersonation pattern: capability in the token (the
   verified `user_type=PLATFORM` claim), target in the request (the acted-for tenant). The
   request-supplied tenant is honoured ONLY on a verified PLATFORM token; a TENANT token is
   always pinned to its own token tenant, and a TENANT request that names an acted-for
   tenant in its body is rejected (4xx), not silently ignored. The write shape is
   discriminated by the verified `user_type`, not by a client-supplied body field.
   This is the one controlled exception to "tenant_id is sole-sourced from the token", it
   applies to PLATFORM requests only, and it is gated on the verified claim.
3. **Lazy role guard retained, no boot-guard parity (parked).** DIS verifies the
   bypass-role posture lazily on first `rls_session` use, not at process start (unlike
   Customer Master's boot guard). This slice relies on the existing lazy guard and does not
   add boot-time parity; recorded as a deliberate deferral with a one-line reason (the guard
   fires before any tenant data is touched; boot parity is a cross-service startup change
   out of scope here).

## Goal

After this slice, DIS supports three session modes expressed through two GUCs
(`app.user_type` + `app.tenant_id`), and the same `dis-ui-server` endpoints serve a
platform user or a tenant user according to the verified token:

- **TENANT, tenant_id = T** : reads and writes tenant T only. Unchanged from today.
- **PLATFORM, tenant_id NULL** : reads every tenant's rows; writes nothing.
- **PLATFORM, tenant_id = T** : reads every tenant (or T); writes only T (impersonation /
  concierge for T).

The catastrophe property holds and is test-proven: a TENANT token can never read or write
another tenant's data, regardless of what its request carries. PLATFORM see-all is wired
and proven through the 6 read endpoints; PLATFORM impersonation-write is wired and proven
through both `/mapping-templates` writes (POST and PATCH). The write-nothing guarantee for
PLATFORM-no-tenant is structural (the policy's WITH CHECK has no PLATFORM branch), not
convention.

## The mechanism (fixed; CC designs the exact text, shows it in plan mode)

### The asymmetric policy

Each of the 13 tenant-scoped policies becomes:

- **USING** (governs read visibility): the row's tenant matches the session tenant, OR the
  session is PLATFORM. So PLATFORM reads widen to all tenants; TENANT reads stay pinned.
- **WITH CHECK** (governs writes): the row's tenant matches the session tenant. No PLATFORM
  branch. So PLATFORM-no-tenant writes nothing (the tenant GUC is NULL, nothing matches),
  and PLATFORM-with-tenant writes only that tenant. This asymmetry is the whole design and
  the deliberate divergence from Customer Master, which put the PLATFORM branch in WITH
  CHECK too and so allowed cross-tenant writes.
- **NULLIF wrapper** on the tenant comparison (`NULLIF(current_setting('app.tenant_id',
  true), '')::uuid`) so a PLATFORM-no-tenant session (empty/unset tenant GUC) does not error
  on the `::uuid` cast; an unset tenant simply matches no rows.
- The `audit.events` outlier keeps its USING-only / no-WITH-CHECK shape and its
  `OR tenant_id IS NULL` branch; it gains the PLATFORM OR-branch in USING only.

CC confirms the exact end-state text per table against the live policies and shows the
field-level before/after in plan mode. The fail-closed read form
`current_setting('app.user_type', true) = 'PLATFORM'` (NULL when unset, never matches) is
the reference; CC verifies it against the live GUC behaviour.

### The three session modes in dis-rls

A PLATFORM session sets `app.user_type='PLATFORM'` and allows `app.tenant_id` to be NULL
(no-tenant) or a specific tenant. A TENANT session sets the tenant and the TENANT
user-type. No session mode is selected by a silent default: a caller states its mode
explicitly. CC proposes the smallest shape that achieves this without a silent default and
shows it for review. Two candidate shapes from grounding (CC picks and justifies, or
proposes better):

- A separate explicit entry point (e.g. `rls_platform_session`) for the two PLATFORM modes,
  leaving `rls_session` as the unchanged TENANT path (the 16 non-token sites untouched), OR
- A required keyword-only mode parameter on `rls_session` (every one of the 27 sites states
  its mode explicitly).

The 15 worker/lib sites and the 1 health probe stay TENANT, set explicitly. The 11
dis-ui-server sites select mode from the token.

### The auth gate (fail-closed, reject-on-ambiguous)

The dev-stub verifier reads an explicit `user_type` claim. The three clean outcomes plus
rejection:

- `user_type = PLATFORM` (verified) : PLATFORM session.
- `user_type = TENANT` with a valid `tenant_id` : TENANT session.
- Anything else (claim absent, empty, unrecognized value, or TENANT with no tenant_id) :
  the request is REJECTED at the door (401/403), no session opened, no query run. Never
  silently downgraded, never defaulted to either mode.

A tenant-tolerant scope resolver lets the 6 read endpoints serve a PLATFORM-no-tenant
identity (returns the all-tenants scope) while keeping TENANT identities pinned. The 2
write endpoints (POST and PATCH `/mapping-templates`) resolve a concrete acted-for tenant
through a write-scope dependency, discriminated by the verified `user_type` (the token,
not a client-supplied body field):

- TENANT: pinned to the token tenant. A TENANT request that names an acted-for tenant in
  the body is REJECTED (4xx invalid body), never silently ignored or dropped.
- PLATFORM (with `dis:ops`) naming an acted-for tenant in the body: writes that tenant
  (the impersonation target lives only in the body; a PLATFORM token carrying a non-empty
  `tenant_id` claim is already rejected at `verify_token`).
- PLATFORM without `dis:ops`, or PLATFORM with no acted-for tenant: clean 403 at the door.

The resolved acted-for tenant threads into the single repo method per write
(`rls_session` for TENANT, `rls_platform_session` for PLATFORM); there is one endpoint and
one repo method per write, no forked platform path. The policy's tenant-pinned WITH CHECK
is the structural backstop: even a mis-resolved tenant cannot write cross-tenant (a clean
rejection, not an RLS violation surfacing as a 500). `require_ops` (the existing `dis:ops`
role gate) is PLATFORM-compatible as-is and is not changed by this slice.

## Scope boundary

In scope:
- The 13-policy rewrite: edit the 13 `schemas/postgres/*.sql` DDL files (one policy per
  per-table file) to the two-GUC end-state, add delta migration `0011` (DROP+CREATE the 13
  policies), add the fresh==migrated catalog-equality test (`test_migration_0011`).
- The `dis-rls` two-GUC session capability (PLATFORM modes, NULL tenant allowed, both GUCs
  set transaction-locally), with no silent default on mode.
- The dev-stub verifier reading and validating the explicit `user_type` claim, with
  reject-on-ambiguous; the dev-stub gains the ability to mint PLATFORM and TENANT tokens for
  tests.
- The tenant-tolerant scope resolver and the PLATFORM-capable dep path for the 6 read
  endpoints (same URLs, token decides), and the PLATFORM impersonation-write path for the 2
  `/mapping-templates` writes (discriminated by verified `user_type`; acted-for tenant in
  the body, gated on PLATFORM; a TENANT body naming an acted-for tenant is rejected).
- Tests wired through real endpoints (see Acceptance), including the catastrophe tests.

Out of scope:
- Concierge AUTHORIZATION (which platform user may act for which tenant): that is Customer
  Master's RBAC, delivered later as token claims. DIS trusts the verified claim and does not
  build grant tables or authorization logic. This slice's gate is the coarse
  PLATFORM/TENANT distinction plus the existing `dis:ops` role, not per-tenant impersonation
  grants.
- The real Customer Master JWKS verifier (13b). The dev-stub stands in; the token contract
  DIS requires (explicit valid `user_type`, reject otherwise) is recorded so 13b inherits
  it.
- Boot-guard parity (register decision 3): the lazy first-use guard is retained.
- `identity_mirror` RLS posture (D41 RLS-OFF stays; the D70 in-query store-list weak link is
  a separate trigger, not reopened here).
- Any new endpoint (same endpoints serve both postures), any DDL beyond the policy text
  (no column/table/schema-object change; "mapping template" work is row DML, not DDL), and
  any change to `services/dis-ui` (READ-ONLY, absolute; frontend is Amit's).
- Worker/lib cross-tenant behaviour: the 15 worker/lib sites stay TENANT, set explicitly;
  no worker gains a PLATFORM mode.
- Mapping-template change AUDITING (recording the acting platform user and an
  impersonation marker on POST/PATCH `/mapping-templates`): deferred to its own later
  slice. These two handlers emit no audit row today; adding auditing is net-new behaviour
  for both tenant and platform writes and is out of 17b's RLS/session/auth scope. The
  acting identity is in scope at these handlers, noted for that future slice only; no audit
  emit is added in 17b. (Depends on 17b having landed `user_type` on `Identity`.)

## Constraints

- **Mechanism not policy.** `'PLATFORM'`/`'TENANT'` are the user-type vocabulary the policy
  and session speak; they are not business rules baked elsewhere. The acted-for tenant and
  the user-type both arrive as handed-in data (verified claim, gated request value), never
  derived or hard-coded.
- **Fresh == migrated, the repo way.** Edit the DDL files to the new end-state AND add the
  delta migration; prove convergence with the catalog-equality test. Never `ALTER POLICY`;
  never edit shipped migrations `0005`/`0007`/`0009`; never re-read DDL files at runtime in
  the new migration (inline the SQL).
- **Tenant from token, except the PLATFORM-gated exception.** TENANT tenant_id is
  token-only. The request-supplied acted-for tenant is honoured only on a verified PLATFORM
  token (register decision 2). No TENANT path ever reads a tenant from a request.
- **No silent default on session mode or user-type.** A missing/unknown `user_type` rejects
  (does not default). A session mode is always stated explicitly by the caller. Set-but-empty
  is an error, not a silent fallback.
- **Write-nothing is structural.** PLATFORM-no-tenant writing nothing must hold because the
  WITH CHECK has no PLATFORM branch, provable by a structural test that survives RLS being
  toggled, not because a code path happens to avoid writes.
- **Role posture inherited, not weakened.** Service code stays on `ithina_dis_user`
  (NOBYPASSRLS); the lazy `_check_posture` guard is retained.
- **`services/dis-ui` untouched**; excluded from all tooling/lint/format/type commands.

## Open questions for plan mode (CC resolves against the live repo; ERROR, not skip)

1. Exact end-state policy text per table (all 13), confirmed against the live
   `pg_policies` USING/WITH CHECK, including the `audit.events` outlier and the NULLIF
   placement. Which of the 13 DDL files each policy lives in (one policy per per-table
   file; the grounding named line
   ranges; CC re-confirms before editing).
2. The `dis-rls` shape decision (separate PLATFORM entry point vs required mode parameter),
   with the call-site blast radius of each and which one best preserves "no silent default".
   How both GUCs are set transaction-locally and how `app.tenant_id` NULL is represented
   (set to NULL/empty vs not set), confirmed against `set_config` behaviour and the policy's
   NULLIF handling.
3. The exact claim name and form for `user_type` (the grounding noted Customer Master uses a
   namespaced claim `https://ithina.com/user_type`; CC confirms what DIS's dev-stub should
   read and mint, and whether DIS mirrors the namespacing).
4. The tenant-tolerant scope resolver shape: how `require_tenant`/`tenant_uuid_of` are
   extended or paralleled so the 6 read endpoints accept PLATFORM-no-tenant while TENANT
   stays pinned, without loosening the TENANT path. Which dep each of the 11 sites uses.
5. The PLATFORM impersonation-write path for POST/PATCH `/mapping-templates`: where the
   acted-for tenant enters the request, how it is gated on the PLATFORM claim, and how a
   PLATFORM-no-tenant write is rejected cleanly (door 403) with the WITH CHECK as backstop.
6. The fresh==migrated proof for `0011`: confirm `downgrade base` then `upgrade head`
   reproduces the two-GUC policies, and that the catalog-equality test compares the live
   policy text against the edited DDL text for all 13.
7. Any gap Task-0 grounding cannot close from the live DB or an existing decision goes on an
   explicit **Gaps and open decisions** list, each with a one-line reason and a route
   (operator call or trigger slice). A plan that resolves these silently is incomplete.

## Acceptance criteria

1. The 13 tenant-scoped policies carry the asymmetric two-GUC form (USING: tenant-match OR
   PLATFORM; WITH CHECK: tenant-match only; NULLIF wrapper), confirmed in live
   `pg_policies`; the `audit.events` outlier keeps its USING-only shape with the PLATFORM
   branch added. The 13 DDL files carry the same end-state text.
2. Fresh == migrated: a fresh bootstrap (`downgrade base` then `upgrade head`) and the
   migrated DB produce identical policy text for all 13 tables, proven by
   `test_migration_0011` (catalog equality), mirroring the 0007/0009 tests.
3. **TENANT unchanged and isolated (the catastrophe property):** a TENANT token for tenant A
   reads and writes only A; it can never read or write B's rows. A TENANT read never widens
   on any request input; a TENANT write naming an acted-for tenant in its body is rejected
   (not silently honoured). Test-proven on at least one read and one write endpoint.
4. **PLATFORM see-all wired and proven:** a verified PLATFORM-no-tenant token returns
   cross-tenant rows through at least one (preferably all 9) read endpoint, and writes
   nothing (a write attempt is rejected cleanly; the WITH CHECK backstop is structurally
   proven).
5. **PLATFORM impersonation-write wired and proven:** a verified PLATFORM token acting for
   tenant T (acted-for tenant supplied in the request, gated on the PLATFORM claim) creates
   (POST) and patches (PATCH) a `/mapping-templates` row for T, and cannot write to any
   other tenant (the WITH CHECK pins it to T), test-proven for both methods.
6. **Reject-on-ambiguous:** a token with `user_type` absent, empty, an unrecognized value,
   or TENANT-with-no-tenant is rejected (401/403) with no session opened, test-proven; no
   request is silently downgraded or defaulted to a mode.
7. **Structural defence of the catastrophe (adversarial target):** tests pin the property,
   not the environment, so they fail if (a) a TENANT identity is treated as PLATFORM, (b)
   the USING widens on anything other than a verified PLATFORM claim, or (c) the WITH CHECK
   stops pinning writes to the token/acted-for tenant. The tests catch a dropped/weakened
   predicate, not merely RLS being off.
8. The lazy `_check_posture` guard still fires (wrong DB or bypass-role raises); service
   code still connects as `ithina_dis_user`; no boot guard added (register decision 3).
9. The request-supplied acted-for tenant is honoured only on a verified PLATFORM token; a
   TENANT request that names an acted-for tenant in its body is REJECTED (4xx), not
   silently ignored or pinned-and-dropped; a TENANT request with no acted-for tenant is
   pinned to its token tenant, test-proven (register decision 2).
10. `make check` / lint / mypy `--strict` clean; tests ship in the same commit;
    `services/dis-ui` unmodified; no DDL beyond the policy text.
