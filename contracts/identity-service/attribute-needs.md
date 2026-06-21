# Identity Service: Attribute Needs

**Purpose.** This document lists every attribute identity-service needs to do its job for the v1.0 launch scope (manual CSV upload from DIS UI). For each attribute: what it is, why identity-service needs it, the expected shape/format, and a placeholder for source (DIS UI passes / Customer Master serves / derived / cached).

**Scope.** v1.0 launch: authenticated user uploads CSV via DIS UI. csv-upload receiver. Streaming consumer. Mirror-sync. dis-ui-server. All other ingress paths (API/webhook, ERP POST, reverse-API) are deferred and out of scope here.

**How to read this.** Each row is an attribute identity-service needs at some point. The Source column is empty for you to decide. The Notes column carries constraints and reasoning.

---

## 1. To verify the caller (every incoming request to identity-service)

identity-service is called by other DIS services (csv-upload, streaming-consumer, dis-ui-server). It must verify the caller is a legitimate DIS service before serving any data.

| Attribute | What | Expected shape | Source | Notes |
|---|---|---|---|---|
| Service-to-service token | Token presented by DIS services calling identity-service | Bearer JWT or mTLS cert | | Verified server-side. Identifies the calling service, not the end user. Issued by cluster service account or equivalent. |

---

## 2. To resolve identity for an authenticated user (csv-upload phase 1 → identity-service)

When the DIS UI uploads a CSV (via csv-upload phase 1), csv-upload calls identity-service to resolve "who is this user, which tenant, which store." identity-service needs enough information to produce a complete `Identity` record.

> **STALE (flagged, not rewritten):** the `^t_[a-z0-9]{12}$`/`^s_[a-z0-9]{12}$` identifier patterns below are the retired invented form (`decisions.md` D52); the DIS fakes now carry Customer Master's `display_code`/`store_code` as the claim values. This document is DIS's input to the unsigned CM contract, so its patterns are corrected at CM sign-off, not here — see `decisions.md` D56.

| Attribute | What | Expected shape | Source | Notes |
|---|---|---|---|---|
| User identity proof | Something that proves the request comes from a logged-in DIS UI user | Customer Master JWT or upload session ID | | The user is already authenticated in DIS UI; this is the artifact that travels with the upload request. Could be the user's JWT itself or a short-lived upload session ID derived from it. |
| JWT signing public key(s) | Public keys to verify the JWT signature | JWKS document, fetchable by URL; standard JWKS format | | If JWT is the user identity proof, identity-service (or its caller) must verify the signature. JWKS URL points to Customer Master. Keys may rotate; identity-service caches but refreshes periodically. |
| JWT issuer | Expected `iss` claim value | String | | identity-service rejects JWTs from any other issuer. |
| JWT audience | Expected `aud` claim value | String (e.g., `dis`) | | identity-service rejects JWTs minted for other audiences. |
| Signing algorithm | Algorithm used by Customer Master to sign JWTs | String (e.g., `RS256`) | | identity-service uses this to select the right verification routine. |
| User ID | Unique identifier for the logged-in user | String, pattern TBD (e.g., `^u_[a-z0-9]{12}$`) | | From the `sub` claim in JWT. Recorded in audit events. |
| Tenant ID | Tenant the user belongs to | `^t_[a-z0-9]{12}$` | | The most load-bearing attribute. Drives RLS, scoping, all downstream writes. |
| Store ID (optional) | Store the user is scoped to, if any | `^s_[a-z0-9]{12}$`, or null | | Some users may be tenant-wide (no store scope). For CSV upload, the user typically uploads on behalf of a specific store; if so, that store_id is here. If null, the upload UI must collect the target store from the user. |
| User roles | What the user is allowed to do within their tenant | Array of strings | | Drives RBAC checks. Expected vocabulary for the DIS UI at launch: `dis:upload` (can upload CSV), `dis:mapping_admin` (can edit/promote mappings), `dis:ops` (Ithina-side ops, cross-tenant), `dis:read` (read-only). The vocabulary is DIS-side; Customer Master is the issuer. |
| Tenant active flag | Whether the tenant is currently active in Customer Master | Boolean | | If the tenant is deactivated, every action by users of that tenant must be rejected. identity-service surfaces this; receivers and dis-ui-server enforce. |
| Store active flag | Whether the store is currently active in Customer Master | Boolean | | Same as tenant active flag, scoped to store. |
| User active flag (optional) | Whether the user record is currently active in Customer Master | Boolean | | A deactivated user should not be able to act even if their token has not expired. Less critical than tenant/store flags but useful for defense in depth. |
| Tenant metadata (optional) | Additional fields about the tenant | Object, free-form | | Examples: PII tokenization policy version, region, source config defaults. Pass-through; identity-service does not interpret. |

---

## 3. To validate a tenant+store at processing time (streaming consumer → identity-service)

When the streaming consumer processes a chunk, it calls identity-service `validate(tenant_id, store_id)` as an FK pre-check. The chunk may have been received minutes or hours ago; the tenant or store could have been deactivated in between. identity-service needs a way to answer "does this pair still exist, and is it active?"

| Attribute | What | Expected shape | Source | Notes |
|---|---|---|---|---|
| Tenant existence | Whether the tenant_id is known to Customer Master | Boolean | | "Known" means a row exists, regardless of active/inactive state. |
| Store existence | Whether the store_id is known to Customer Master | Boolean | | Same as tenant existence, scoped to store. |
| Tenant active flag | Same as item in §2 | Boolean | | Reused for validate. |
| Store active flag | Same as item in §2 | Boolean | | Reused for validate. |
| Cache freshness signal | Whether the answer was fresh, stale, or from the fallback mirror | Enum: `cache_fresh`, `cache_stale`, `customer_master`, `identity_mirror_fallback` | (computed by identity-service) | identity-service computes this itself based on cache age + Customer Master health. Not externally sourced. |

---

## 4. To maintain the local mirror (mirror-sync-consumer + identity-service share this need)

When a tenant or store record changes in Customer Master, the identity_mirror in the data-platform Postgres must update. Two consumers: identity-service (refreshes its cache) and mirror-sync-consumer (writes the mirror).

| Attribute | What | Expected shape | Source | Notes |
|---|---|---|---|---|
| Change event delivery | A channel that signals "tenant or store record changed" | Pub/Sub message, webhook, or polling response | | Mechanism choice has tradeoffs: Pub/Sub is timely and decoupled, webhook adds inbound surface to DIS, polling is laggy but simplest. |
| Event ID | Unique identifier for each change event | UUID | | For idempotency. mirror-sync uses this as the dedupe key for at-least-once Pub/Sub semantics. |
| Event type | What kind of change | Enum: `created`, `updated`, `deactivated` | | Soft-delete only; no `deleted` (mirror keeps deactivated rows because canonical references them). |
| Entity type | Tenant or store | Enum: `tenant`, `store` | | Dispatch by entity. |
| Entity ID | The tenant_id or store_id affected | `^t_[a-z0-9]{12}$` or `^s_[a-z0-9]{12}$` | | Primary key of the affected row. |
| Owning tenant ID | For store events, the parent tenant; for tenant events, equals entity_id | `^t_[a-z0-9]{12}$` | | Lets mirror-sync apply RLS-aware writes uniformly. |
| Source timestamp | When the change happened in Customer Master | ISO 8601 UTC | | Conflict-resolution key for out-of-order delivery. Older source_ts never overwrites newer state. |
| Post-change snapshot | The full current state of the entity | Object (tenant or store record, including is_active flag and metadata) | | mirror-sync projects what it needs; identity-service uses for cache refresh. Carrying the full state simplifies reasoning vs. diffs. |

---

## 5. Things identity-service computes itself

For completeness, attributes identity-service derives or computes internally. Listed so the boundary is clear: these are *not* needed from external sources.

| Attribute | What | Notes |
|---|---|---|
| `resolved_at` | Timestamp when identity record was last refreshed | Set by identity-service on every cache write. |
| `source` (enum) | Whether response was cache_fresh, cache_stale, customer_master, identity_mirror_fallback | Computed from cache state + Customer Master health. |
| Cache key | Internal key for cache lookups | Constructed from request artifact (JWT, session ID, tenant_id+store_id). |
| Circuit breaker state | Open / closed / half-open for Customer Master health | Internal observability state. |
| `trace_id` for server-side request tracking | UUID | Generated per request for log correlation. Different from any caller-side trace_id. |

---

## 6. Open questions to resolve

Pinning these answers fills the Source column above.

- **What artifact does the DIS UI hand to csv-upload phase 1?** The user's JWT directly, or an upload-session ID derived from it? This decides which method identity-service exposes (resolve_from_token vs resolve_from_upload) and what Customer Master needs to issue.
- **Does the JWT carry tenant_id, store_id, roles as claims?** If yes, identity-service can extract them locally. If no, identity-service must call Customer Master for resolution on every cache miss.
- **Is store_id always in the JWT, or sometimes resolved separately?** If a user is tenant-wide (not store-scoped), the UI must collect store_id at upload time. That changes the upload flow shape.
- **How does Customer Master communicate tenant/store changes to DIS?** Pub/Sub, webhook, or polling. Pub/Sub is the architecture default; webhook and polling are alternatives if Pub/Sub is not on Customer Master's roadmap.
- **What endpoint can identity-service call on Customer Master for cache miss?** Customer Master needs a `GET /tenants/{tenant_id}/stores/{store_id}` (or equivalent) returning the active/inactive state and metadata. Same endpoint serves both resolve cache-miss and validate calls.
- **JWKS URL, issuer, audience, signing algorithm.** These are configuration values DIS needs to verify JWTs. They're stable once chosen; just need to be confirmed.

---

## 7. What identity-service does NOT need from Customer Master

For boundary clarity, things people sometimes assume identity-service needs but actually doesn't:

- **User passwords or credentials.** Customer Master handles authentication entirely. identity-service only sees post-auth tokens.
- **The user's email, name, or display fields.** Not used by DIS data plane. dis-ui-server may want them for audit display; that's a separate dis-ui-server → Customer Master call, not an identity-service concern.
- **RBAC policy definitions.** identity-service just receives the user's `roles` list and surfaces it. The mapping of "role X can do Y" is enforced in dis-ui-server (for UI actions) and receivers (for data-plane actions), not in identity-service.
- **Audit logs of user activity.** Customer Master's audit and DIS's audit are separate concerns.
- **OAuth flows, login screens, password resets.** Customer Master owns these entirely.

---

*End of attribute needs document. Once you fill in the Source column or resolve the open questions in §6, this document becomes the basis for the Customer Master contract document and locks in the identity-service implementation.*
