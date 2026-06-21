# dis-ui-server contract (consumed by DIS UI)

What the DIS UI expects from `dis-ui-server`. This doc is **seeded from in-repo
sources**, not from a canonical demand list (which is absent): the handler set
comes from `docs/architecture.md` section 4.17, the auth model from
`services/dis-ui-server/` CLAUDE.md and README, and the GET /me shape from the
slice-19 fixture (`src/lib/dis-ui-server/fixtures.ts`).

**Every shape here is PROVISIONAL.** Reconcile against the canonical sources when
they land: `docs/ui-engineer-demand-list.md` section 1.1 (the endpoint inventory)
and `docs/decisions.md` D25 (the Customer Master RBAC claim vocabulary).

## Auth model

- The UI sends a Customer Master bearer token on every dis-ui-server call.
- dis-ui-server verifies the JWT against Customer Master's JWKS and extracts the
  claims that Sanjeev's slice-2 fake pins (PROVISIONAL pending D25): `sub`,
  `tenant_id`, `store_id`, and a `roles` array (e.g. `dis:upload`, `dis:read`,
  `dis:ops`, `dis:mapping_admin`). RBAC is enforced at the handler level
  (tenant-scoped handlers require `tenant_id`; ops-only handlers require the
  `dis:ops` role).
- On token expiry dis-ui-server returns 401 and the UI refreshes (real mode).
- Slice 19/20 substitutes an HMAC-signed stub JWT verified locally with the same
  issuer/audience (`https://customer-master.local` / `dis`) and claim set;
  `src/auth/verifyToken.ts` is the single seam that swaps HMAC for JWKS (slice 13).
  The UI decodes these claims into its `AuthSnapshot` (`userId`, `tenantId`,
  `storeId`, `roles`); profile fields are NOT token claims (see GET /me).

## Handler set (architecture section 4.17)

| Handler | Purpose | Scope |
|---|---|---|
| `upload_session` | Start a CSV upload (Phase 1): issue a signed PUT URL (D36) | Tenant |
| `sample_upload` | Submit an onboarding sample for inference | Tenant / ops |
| `onboarding_review` | Review and promote mappings (staged -> active) | Tenant / ops |
| `mapping_crud` | CRUD on `config.source_mappings` (new version per edit) | Tenant |
| `quarantine` | Quarantined rows: tenant slice and cross-tenant ops slice | Tenant + ops |
| `audit` | Lookup `audit.events` by trace_id / tenant / store / time | Tenant (ops cross-tenant) |
| `duckdb_query` | Ad-hoc SQL over a GCS bronze blob | Ops only |
| `auth` | Cross-cutting JWT verification (FastAPI dependency, not an endpoint) | All |

**Note:** architecture 4.17 lists **no `GET /me` endpoint**. See Open questions.

## GET /me (PROVISIONAL profile call - OPEN)

The UI's `getMe()` returns the signed-in user's display profile:

```ts
type MeResponse = {
  user_id: string
  email: string
  name: string
  tenant_id: string | null      // null for ops (cross-tenant)
  tenant_name: string | null    // null for ops; server-side display join
}
```

These are profile/display fields, **none of which are token claims**. The token
carries only `sub` / `tenant_id` / `store_id` / `roles`; `email`, `name`, and
`tenant_name` come from a **separate dis-ui-server -> Customer Master profile
call** (`contracts/identity-service/attribute-needs.md` routes user email/name/
display fields there, explicitly out of the data-plane identity-service). Whether
that call is a `GET /me` endpoint or another shape is OPEN (see Open questions).
In fixture mode the UI returns hardcoded profile fixtures keyed by `sub`.

## Open questions

1. **The profile call.** Identity/authz comes from the token claims (`sub`,
   `tenant_id`, `roles`), which the UI decodes locally. The display profile
   (`email`, `name`, `tenant_name`) is a separate dis-ui-server -> Customer Master
   call; architecture 4.17 lists no `GET /me` handler, so whether dis-ui-server
   exposes one is OPEN, pending Sanjeev and slice 13. The UI's `getMe()` models it
   behind fixtures and is correct either way.
2. **RBAC vocabulary.** The token's `roles` array uses a `dis:<capability>`
   namespace (`dis:upload`, `dis:read`, `dis:ops`, `dis:mapping_admin`) - the
   PROVISIONAL values from Sanjeev's slice-2 fake / `attribute-needs.md`, pending
   `decisions.md` D25 (the recovered surface map used an admin-frontend 4-tuple;
   D25 settles which is canonical). Phase 1 gates only on the `dis:ops` role and
   `tenant_id`; no UI gates on fine-grained permissions.
3. **External id vs UUID (D37).** Token/contract ids are external strings
   (`t_*` / `s_*` / `u_*`); the DB keys by UUID. The translation location is OPEN
   (`decisions.md` D37, hard deadline Slice 7). The UI only ever sees the external
   form.
4. **`/me` caching.** The `getMe` query uses `staleTime: Infinity` (identity is
   stable within a session). Revisit when real mode and tenant-switching exist.

## Canonical sources to reconcile against

- `docs/ui-engineer-demand-list.md` section 1.1 (the endpoint inventory).
- `docs/decisions.md` D25 (Customer Master claim vocabulary, still open) and D37
  (external id vs UUID translation, open).
- `libs/dis-testing/src/dis_testing/fixtures.py` and
  `contracts/identity-service/attribute-needs.md` (Sanjeev's provisional claim set).
