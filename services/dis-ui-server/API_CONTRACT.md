# dis-ui-server API contract

**Status:** v1.0 contract, 2026-06-05. Implementation-grade: dis-ui-server is implemented from this document without re-inspecting the frontend. Derived from what `services/dis-ui` actually calls and expects; reconciled against `docs/decisions.md`, `docs/architecture.md` §4.16/§4.17, `services/dis-ui-server/README.md` + `CLAUDE.md`, the live DDL in `schemas/postgres/`, and the libs this service must reuse.

**Sources of truth and precedence** (the precedence order is set by `services/dis-ui/docs/dis-ui-surface-map.md`'s own header):

1. **Frontend code** — `services/dis-ui/src/lib/dis-ui-server/*` (`types.ts`, `client.ts`, `mode.ts`, per-module clients, fixtures, tests). Pins every request/response **shape** exactly. The frontend is the source of truth for shapes; this contract adapts the backend to it, never the reverse.
2. **`docs/ui-engineer-demand-list.md`** (RECONSTRUCTION v0.2) — authoritative for **paths, verbs, query params, and scope tags**. Path convention `/v1/<group>/<resource>` (RECOVERED; operator-confirmed for this contract). Onboarding shapes 2.1/2.2 are RECOVERED verbatim.
3. **`services/dis-ui/docs/dis-ui-server-contract.md`** — the auth/token model the UI was built against.
4. **Backend invariants** — root `CLAUDE.md` hard rules; decisions D17, D22, D25, D26, D34, D36, D37, D42, D49, D51–D56; this service's README/CLAUDE.md write scope.

**Path authority note.** The frontend's real mode is wired but unimplemented (slice 13): `client.ts` is the single fetch seam and no HTTP path is hardcoded in dis-ui today. Paths in this contract therefore come from the demand list; shapes come from the frontend type modules, quoted field-for-field. Wiring dis-ui's real mode to these paths is the planned slice-13 frontend change and is not a reshape of the frontend's contract.

---

## Blockers

Each item is a conflict between what the frontend expects and what the backend can currently provide, requiring an operator decision. None are resolved here; endpoint shapes are still fully specified below so implementation can proceed everywhere else.

1. **Notifications have no backing store and no emitter.** The UI requires four endpoints (§6) plus a 30-second unread-count poll (`src/lib/dis-ui-server/notifications.ts`; `src/components/NotificationBell.tsx`). No schema in `schemas/postgres/` holds notifications, no service emits them (surface map §9 lists the trigger events: rows quarantined, shadow ready, mapping promoted/deprecated, source health degraded), and `services/dis-ui-server/CLAUDE.md` pins this service's writes to `config.source_mappings` + two Pub/Sub topics only — a notifications table is outside that write scope. Needs: a decision on where notifications persist, who writes them, and a `decisions.md` entry before §6 can be implemented.

2. **The source registry does not exist.** The UI's `Source` record (`sources.ts`: `source_id`, `name`, `type`, `store`, `status`, `active_version`, `quarantine_rate_24h`, `last_ok_at`) and `POST /v1/sources` (SourceCreate, slice 27) have no backing table: `config.source_mappings` keys sources by `(tenant_id, source_id)` string but holds no display `name`/`type`/`store` metadata and no source-level status. `sources.ts` itself flags this (FM3): "WHERE source registration lives [is] Sanjeev's to confirm." `active_version` and mapping-derived fields are computable from `config.source_mappings`; the display metadata and the create/update/deprecate writes are not. Needs: a source-registry decision (new `config.sources` table would expand this service's write scope).

3. **DuckDB query request shape conflicts, and the execution contract is undefined.** The frontend sends `{ "sql": string }` only (`ops-query.ts: QueryRequest`) with sample SQL reading `FROM bronze`; demand list 7.4 and this service's README pin the body as `{ "gcs_uri", "sql" }`. With sql-only, the server must define what `bronze` resolves to. Read-only enforcement, row caps, timeout, and the cross-tenant authorization posture are all explicitly left open by slice 26. Needs: operator decision on the body shape (frontend wins per this contract's rules, but then the bronze-resolution model must be decided) and the safety model.

4. **Quarantine chain depth / resubmit history has no storage.** The UI requires `chain_depth`, `resubmits[]` on the detail, a minted child `trace_id` + incremented `chain_depth` on the resubmit response, and backend cap enforcement at 3 (`quarantine.ts`; architecture §6.5). `quarantine.quarantined_rows` has no chain columns; `audit.events` lacks `prior_trace_id` (D42 OPEN, duplicate/lineage detail relegated to `event_data` JSONB). There is no durable place to record a resubmit chain that this service is allowed to write. Needs: a lineage-storage decision (schema addition or a pinned `event_data` convention) before 4.2/4.3 can return real chain data.

5. **`GET /v1/me` profile fields have no DIS source, and the handler itself is unconfirmed.** `email` and `name` exist only in Customer Master; the dis-ui-server → Customer Master profile call is OPEN (architecture 4.17 lists no `/me` handler; D56 keeps CM contract shapes unsigned). The endpoint must exist — `AppLayout` calls it on every authenticated mount. `tenant_name` IS servable from `identity_mirror.tenants.name`. The same CM-user-profile gap hits `MappingVersion.created_by` (§3.1): the UI expects a display name; the schema holds `created_by_user_id UUID` and DIS has no user-name source. Needs: the CM profile contract, or an interim decision (serve the UUID string / a placeholder).

6. **Health vocabulary derivation is undefined.** `healthy | warning | failing` (dashboard sources, fleet summary/tenants) and the `failing` member of `SourceStatus` have no defined derivation rule anywhere. `rows_24h`, `quarantined_open`, and latency percentiles are mechanically derivable from `audit.events` (`row_count`, `duration_ms`) and `quarantine.*`; the classification thresholds are a product decision. Needs: pinned thresholds (e.g. quarantine-rate and staleness cutoffs per state).

7. **`AuditTrace.source_id` is not an `audit.events` column.** The UI requires `source_id` on the trace response (`audit.ts: AuditTrace`). `audit.events` (23 columns) has no `source_id`; it is recoverable only from `event_data` JSONB if every emitting service includes it — a cross-service convention not yet pinned. Needs: either the convention pinned in the audit-emission contract, or a schema addition (adjacent to D42).

8. **Shadow stats bookkeeping is a sequencing dependency.** `staging.*` tables exist (output target), but `window`, `input_chunks`, and `validation_pass_rate` (`shadow.ts: ShadowStats`) require staged-mapping shadow execution (streaming-consumer side) and its audit conventions, which are not built. Shapes are specified below; the endpoints cannot return real data until the shadow-execution slice lands. Needs: acknowledgment of ordering (not a shape decision).

9. **`fixed_file` resubmit carries no file reference.** Demand list 4.3 pins the resubmit body to `{ resubmit_type, parent_trace_id }`, and the frontend implements exactly that — but architecture §6.5's `fixed_file` flow implies a corrected upload (fresh bronze) the body cannot reference. The frontend itself surfaces this and deliberately does not invent a field (`quarantine.ts` comment). Needs: the real `fixed_file` flow definition (likely: an upload-session reference added to 4.3 in a coordinated frontend+backend change).

---

## 1. Implementation basis

Restated from root `CLAUDE.md` — the stack this contract is implemented on:

- **FastAPI + uvicorn.** Handlers are thin `APIRouter`s per sub-module (`handlers/`), mounted by `main.py` (see README structure). No business logic in handlers.
- **Domain errors, not `HTTPException`.** Every failure raises a named error from `libs/dis-core/errors.py`; FastAPI exception handlers map them to status codes and the error envelope (§2.3). New error classes this service needs are listed in §2.3 and land in dis-core, not locally.
- **Postgres via `libs/dis-rls` only.** Every canonical/config/quarantine/audit read or write runs inside `async with rls_session(engine, tenant_id) as s:` (SQLAlchemy 2.0 async under the hood; never raw sessions). Ops cross-tenant reads use the platform-scoped session variant — see §2.2.
- **Pydantic v2** request/response models (orjson backend). Models below are normative.
- **UUIDv7** for every minted identifier, via the dis-core helper (`new_uuid7`). Never `uuid.uuid4`.
- **GCS via `libs/dis-storage` only** (`build_object_path`, `generate_upload_url`, `StorageClient`, `split_object_uri`). Required extension noted in §9.
- **Audit via `libs/dis-audit`** (`AuditEvent` + writer `emit()`), fire-and-forget: audit failures are logged, never raised to the caller (hard rule 11).
- **Pub/Sub envelopes** are the frozen contracts in `contracts/pubsub/` — this service publishes `mapping.changed`, `ingress.resubmit`, and `csv.received` only.
- **Logging:** dis-core structlog-style binding; every log line carries `tenant_id`, `trace_id` (where applicable), `service="dis-ui-server"`, `stage`.
- **Tracing:** OpenTelemetry; `trace_id` minted only in `POST /v1/csv-uploads` (Slice 8, the synchronous form) and `POST /v1/quarantine/{trace_id}/resubmit` (both ingress-starting actions — see §8 and §4.3); never anywhere else.

This service **never writes canonical tables** (D26). Its writes: `config.source_mappings` rows/status, the three Pub/Sub topics above, and GCS onboarding-staging objects. Every endpoint below states its side effects; none implies a canonical write.

---

## 2. Conventions

### 2.1 Auth seam

Identity/auth is **parked** (D25/D56 OPEN). Every protected endpoint depends on one FastAPI dependency so real auth slots in later without reshaping handlers:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Identity:
    user_id: str                # token `sub`
    tenant_id: str | None       # None for ops (cross-tenant) users
    store_id: str | None
    roles: tuple[str, ...]      # `dis:<capability>` namespace

async def get_current_identity(request: Request) -> Identity: ...
# Dependency variants built on it:
#   require_tenant(identity)  -> Identity with tenant_id guaranteed (403 TenantScopeError otherwise)
#   require_ops(identity)     -> Identity with "dis:ops" in roles (403 OpsRoleRequiredError otherwise)
```

- The UI sends `Authorization: Bearer <token>` on every call (`client.ts`). No cookies, no CSRF surface.
- Claims (pinned by the UI's `verifyToken.ts` and `dis-ui-server-contract.md`): `sub`, `tenant_id`, `store_id`, `roles: string[]`. Role vocabulary (PROVISIONAL pending D25): `dis:upload`, `dis:read`, `dis:ops`, `dis:mapping_admin`. Phase 1 gates only on `dis:ops` vs `tenant_id` presence; no endpoint gates on finer roles.
- **DEV STUB:** verify HMAC HS256 with secret `dis-ui-dev-stub-secret-not-for-production`, issuer `https://customer-master.local`, audience `dis` — byte-identical to the UI's `/dev/login` stub issuer so dev tokens round-trip. The JWKS swap (Customer Master, D25) replaces only the verifier; `Identity` and the dependencies are stable.
- Token invalid/expired → **401** (`AuthTokenError`). The UI re-authenticates; dis-ui-server never refreshes tokens.

### 2.2 Tenant scoping and ops cross-tenant

- Tenant-scoped endpoints resolve `tenant_id` **from the token only** — never from a body or query param. Postgres access then runs under `rls_session(engine, tenant_id)`; RLS is the isolation mechanism (hard rule 1).
- Ops (`dis:ops`) endpoints read cross-tenant. RLS-enabled tables (`quarantine.*`) require a platform-scoped read path; how ops bypasses per-tenant RLS (a `dis-rls` platform-session variant vs. per-tenant fan-out) is an implementation decision inside `libs/dis-rls` — it must not be raw SQLAlchemy (hard rule 1). `identity_mirror.*` is not RLS-enabled (D41) and reads directly via the rls helper's session.
- Identifier semantics: `tenant_id`/`user_id` values on the wire are **opaque strings** to the UI. Per D37 (RESOLVED) + D52/D55 (the `t_*`/`s_*` form is a retired DIS invention), the real values are the **internal UUIDs** serialized as lowercase strings. The frontend types them as plain `string` and never parses them, so serving UUIDs requires no frontend change; the `t_acme9k2l1mn4`-style values in dis-ui fixtures and dev personas are placeholders pending D56 (JWT claim value sign-off). The dev-stub token minted for local runs should carry the seeded mirror tenant UUID so RLS reads resolve.
- `source_id` is a kind-style string key (e.g. `manual_csv_upload`), exactly as in `config.source_mappings.source_id` — same value end to end, no translation.

### 2.3 Errors

The frontend's `client.ts` throws on any non-2xx **without parsing the body** — so the error envelope is backend-defined and additive. Envelope (all error responses):

```json
{
  "error": {
    "code": "mapping_state_conflict",
    "message": "source manual_csv_upload has no staged version",
    "trace_id": "0190ac0e-…",
    "details": {"tenant_id": "…", "source_id": "manual_csv_upload"}
  }
}
```

`code` is the snake_case error name; `message` is human-readable and PII-free; `trace_id` present when one is in scope; `details` carries the load-bearing identifiers (code-quality rule 5: every error carries `tenant_id`, `trace_id`, and the relevant id).

Domain error → status mapping (FastAPI exception handlers; existing dis-core errors first):

| dis-core error | Status | Used by |
|---|---|---|
| `IdentityNotFoundError` | 404 | (future identity-service consumers; the Slice 8 upload resolves via mirror reads instead) |
| `IdentityServiceUnavailableError` | 503 + `Retry-After` | (future identity-service consumers) |
| `RlsContextError` | 500 | any DB endpoint (misconfig) |
| `StorageError` | 503 | csv-uploads GCS write (LIVE, Slice 8 — retryable dependency); sample upload, quarantine payload fetch |
| `MappingConfigError` | 400 | mapping override/approve (invalid rules) |
| `MappingInputError` | 422 | dry-run (sample violates engine contract) |
| `ValidationSuiteError` | 500 | dry-run / validation-draft internals |
| `PayloadTooLargeError` | 413 | csv-uploads (LIVE, Slice 8): mid-stream ceiling + Content-Length early check |
| `UploadRequestError(part)` | 400 | csv-uploads (LIVE): malformed multipart; values never echoed |
| `UploadStructureError(reason)` | 422 | csv-uploads (LIVE): tier-0 structural gate (D51) |
| `StoreStateConflictError` | 409 | csv-uploads (LIVE): store resolved but not ACTIVE (after the 404 resolve) |
| `EventPublishError(topic)` | 503 | csv-uploads (LIVE): publish failed after the GCS write (accepted orphan) |

New error classes this service requires — **to be added to `libs/dis-core/errors.py`** (conventions: subclass `DisError`, keyword-only context fields):

| New error | Status | Semantics |
|---|---|---|
| `AuthTokenError(reason)` | 401 | missing/expired/malformed token, bad claims |
| `TenantScopeError(tenant_id)` | 403 | token lacks `tenant_id` for a tenant endpoint, or resource belongs to another tenant |
| `OpsRoleRequiredError()` | 403 | `dis:ops` missing on an ops endpoint |
| `ResourceNotFoundError(resource, identifier, tenant_id)` | 404 | generic not-found for throw-style lookups (quarantine detail, notification id, sample id) |
| `MappingStateConflictError(source_id, expected, actual)` | 409 | promote/reject with no staged version; concurrent transition |
| `ResubmitChainCapError(trace_id, chain_depth)` | 409 | resubmit at/over cap 3 (architecture §6.5) |
| `RateLimitedError(retry_after)` | 429 + `Retry-After` | upload rate cap (demand-list conventions: `RateLimited` RECOVERED) — not yet built |
| `DuckDbQueryError(message)` | 400 | invalid SQL/URI (message is surfaced; the UI renders it SQL-style) |
| `DuckDbTimeoutError(cap_seconds)` | 504 | query exceeded the configured cap |

Body-shape validation failures (malformed JSON, wrong types) use FastAPI's standard 422 with the same envelope via a validation exception handler.

### 2.4 Success responses: no envelope, and the nullable-lookup rule

- **No success envelope.** `client.ts` does `(await response.json()) as T` — response bodies are the bare resource/array shown per endpoint. Never wrap in `{data: …}`.
- **Nullable-lookup rule.** Where the frontend types a lookup as `T | null` and renders an EmptyState on `null`, the server returns **200 with JSON body `null`** for not-found, NOT 404 (a 404 would throw in `client.ts` and degrade the UX to an error state). Applies to: `GET /v1/dashboard/summary`, `GET /v1/sources/{source_id}`, `GET /v1/sources/{source_id}/mappings/{version}`, `GET /v1/sources/{source_id}/shadow-stats`, `GET /v1/audit/{trace_id}`. Lookups the frontend treats as throwing (`GET /v1/quarantine/{trace_id}`, notification ids, sample ids) use 404 `ResourceNotFoundError`. Each endpoint below states which rule it follows.

### 2.5 Lists, pagination, filtering

List responses are **bare JSON arrays** (frontend shape). The frontend sends no pagination params today and filters client-side; therefore:

- Pagination params are **optional and additive**: `?limit=` (default and max documented per endpoint; default behavior without params must return the full bounded set the UI expects) and `?offset=` (default 0). No cursor envelope is possible without breaking the array shape.
- Server-side caps: quarantine list 500 rows (newest first), notifications 200, sources/mappings/fleet unbounded at beta scale (5 tenants), audit search N/A (not in this surface).
- Filter params come from the demand list and are optional; the server must also tolerate their absence (the UI may keep filtering client-side).
- Ordering: every list has a documented stable default order (stated per endpoint).

### 2.6 Backend → UI enum translations

The BFF owns vocabulary translation; DB vocab never leaks to the UI.

| Domain | DB / lib value | UI value |
|---|---|---|
| Quarantine status | `NEW` | `open` |
| | `RESOLVED`, `DISMISSED` | `resolved` |
| Quarantine failure stage | `PRE_MAPPING_VALIDATION` | `source-shape` |
| | `POST_MAPPING_VALIDATION` | `canonical-shape` |
| | `IDENTITY_VALIDATION` | `fk` |
| | `MAPPING_EXECUTION` | `normalization` |
| | `CANONICAL_WRITE`, `OTHER` | `canonical-shape`, `source-shape` respectively — **PROVISIONAL**: the UI enum has no member for these; mapping chosen to the nearest user-meaningful bucket. Flagged for reconciliation alongside Blocker 6. |
| Mapping status | `STAGED` / `ACTIVE` / `DEPRECATED` | `staged` / `active` / `deprecated` |
| | `DRAFT` | never surfaced in version lists (onboarding-internal; see §2.2 onboarding notes) |
| Audit stage (dis-audit `Stage`) | `RECEIVED` | `received` |
| | `PII_TOKENIZED`, `BRONZE_WRITTEN`, `INGRESS_PUBLISHED`, `MAPPING_LOOKED_UP` | collapsed into `received` (chunk pre-processing; PROVISIONAL — UI renders any string, so a finer mapping is additive) |
| | `IDENTITY_VALIDATED`, `PRE_MAPPING_VALIDATED` | `validated` |
| | `MAPPING_EXECUTED`, `POST_MAPPING_VALIDATED` | `mapped` |
| | `CANONICAL_WRITTEN` | `committed` |
| | `QUARANTINED` | `quarantined` |
| Audit outcome | `SUCCESS` / others | `ok` / `error` (UI `AuditStage.status`) |
| Mapping version number | `version_seq_per_source` (SMALLINT) | `version` (the UI's per-source integer; the global `mapping_version_id` BIGSERIAL appears only as `mapping_version_id` in audit stages) |

### 2.7 Polling

- `GET /v1/notifications/unread-count`: polled every 30 s per client (surface map §6.5). Must be cheap (single indexed count); `Cache-Control: no-store`.
- `GET /v1/onboarding/samples/{sample_id}`: polled during analysis until `status` ∈ {`ready`,`failed`}. Analysis is in-process; if synchronous within the POST is feasible (≤ a few seconds at 10 MB cap), the GET simply returns `ready` immediately — the UI handles both.
- `GET /healthz`: unauthenticated liveness, returns `{"status": "ok"}`.

---

## 3. Summary table

| # | Method | Path | Screen (call site) | Auth | Writes data |
|---|---|---|---|---|---|
| 1 | GET | /v1/me | AppLayout header (`me.ts`) | tenant or ops | n |
| 2 | GET | /v1/dashboard/summary | Dashboard (`dashboard.ts`) | tenant | n |
| 3 | GET | /v1/sources | SourcesIndex, SampleUpload, Dashboard (`sources.ts`) | tenant (ops: `?tenant_id=`) | n |
| 4 | GET | /v1/sources/{source_id} | SourceEdit (`sources.ts:getSource`) | tenant | n |
| 5 | POST | /v1/sources | SourceCreate (`sources.ts:createSource`) | tenant | y (Blocker 2) |
| 6 | PATCH | /v1/sources/{source_id} | SourceEdit (`sources.ts:updateSource`) | tenant | y (Blocker 2) |
| 7 | POST | /v1/sources/{source_id}/deprecate | SourcesIndex (`sources.ts:deprecateSource`) | tenant | y (Blocker 2) |
| 8 | POST | /v1/onboarding/samples | SampleUpload (`onboarding.ts:createSample`) | tenant or ops | y (GCS staging + DRAFT mapping) |
| 9 | GET | /v1/onboarding/samples/{sample_id} | SampleUpload poll, MappingReview (`onboarding.ts:getSample`) | tenant or ops | n |
| 10 | PATCH | /v1/onboarding/samples/{sample_id}/mapping | MappingReview (`onboarding.ts:patchSampleMapping`) | tenant or ops | y (DRAFT mapping) |
| 11 | POST | /v1/onboarding/samples/{sample_id}/dry-run | MappingReview (`onboarding.ts:dryRunSample`) | tenant or ops | n |
| 12 | POST | /v1/onboarding/samples/{sample_id}/approve | MappingReview (`onboarding.ts:approveSample`) | tenant or ops | y (DRAFT→STAGED) |
| 13 | GET | /v1/sources/{source_id}/mappings | MappingVersions (`mappings.ts`) | tenant | n |
| 14 | GET | /v1/sources/{source_id}/mappings/{version} | MappingVersions detail (`mappings.ts`) | tenant | n |
| 15 | GET | /v1/sources/{source_id}/shadow-stats | Shadow (`shadow.ts`) | tenant | n |
| 16 | GET | /v1/sources/{source_id}/shadow-diff | Shadow (`shadow.ts`) | tenant | n |
| 17 | POST | /v1/sources/{source_id}/promote | Shadow (`shadow.ts:promoteShadow`) | tenant | y (STAGED→ACTIVE + `mapping.changed`) |
| 18 | POST | /v1/sources/{source_id}/reject | Shadow (`shadow.ts:rejectShadow`) | tenant | y (STAGED→DEPRECATED + `mapping.changed`) |
| 19 | GET | /v1/quarantine | QuarantineConsole (`quarantine.ts`; ops: `ops-cross-tenant.ts`) | tenant (ops: cross-tenant) | n |
| 20 | GET | /v1/quarantine/{trace_id} | QuarantineConsole detail (both modules) | tenant (ops: cross-tenant) | n |
| 21 | POST | /v1/quarantine/{trace_id}/resubmit | QuarantineConsole (`quarantine.ts:postResubmit`, `ops-cross-tenant.ts:postOpsResubmit`) | tenant (ops: cross-tenant) | y (`ingress.resubmit` publish) |
| 22 | GET | /v1/audit/{trace_id} | AuditLookup (`audit.ts`; ops: `ops-cross-tenant.ts`) | tenant (ops: cross-tenant) | n |
| 23 | GET | /v1/notifications | Notifications (`notifications.ts`) | tenant | n (Blocker 1) |
| 24 | GET | /v1/notifications/unread-count | NotificationBell (`notifications.ts`) | tenant | n (Blocker 1) |
| 25 | PATCH | /v1/notifications/{id}/read | Notifications (`notifications.ts:markNotificationRead`) | tenant | y (Blocker 1) |
| 26 | POST | /v1/notifications/mark-all-read | Notifications (`notifications.ts:markAllNotificationsRead`) | tenant | y (Blocker 1) |
| 27 | GET | /v1/ops/fleet/summary | OpsFleet (`ops-fleet.ts`) | ops | n |
| 28 | GET | /v1/ops/fleet/tenants | OpsFleet (`ops-fleet.ts`) | ops | n |
| 29 | POST | /v1/ops/duckdb/query | OpsQuery (`ops-query.ts:executeQuery`) | ops | n (Blocker 3) |
| 30 | POST | /v1/csv-uploads | **BUILT (Slice 8)**; no UI call site yet — synchronous upload, supersedes the signed-URL 8.1/8.2 pair | tenant | y (GCS object write + `csv.received` publish + audit) |
| — | GET | /healthz | (infra) | none | n |

---

## 4. Shared models

Normative Pydantic v2 models. Field names, optionality, and enums are verbatim-aligned to the frontend type modules cited in each docstring. `from __future__ import annotations`; all timestamps are ISO-8601 UTC strings on the wire (`datetime` fields serialized with `Z`).

```python
from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, Field

# ---- vocabulary (frontend-pinned) ------------------------------------------

class SourceStatus(StrEnum):           # sources.ts:6
    active = "active"
    staged = "staged"
    deprecated = "deprecated"
    failing = "failing"                # derivation: Blocker 6

class SourceHealth(StrEnum):           # dashboard.ts:13 / ops-fleet.ts:24 (FleetHealth, identical)
    healthy = "healthy"
    warning = "warning"
    failing = "failing"

class MappingStatus(StrEnum):          # mappings.ts:11 (no DRAFT on the wire)
    active = "active"
    staged = "staged"
    deprecated = "deprecated"

class FailureStage(StrEnum):           # quarantine.ts:11
    source_shape = "source-shape"
    canonical_shape = "canonical-shape"
    fk = "fk"
    normalization = "normalization"

class QuarantineStatus(StrEnum):       # quarantine.ts:12
    open = "open"
    resolved = "resolved"

class ResubmitType(StrEnum):           # quarantine.ts:52
    replay = "replay"
    fixed_file = "fixed_file"

class Severity(StrEnum):               # notifications.ts:13
    info = "info"
    warning = "warning"
    error = "error"

class NotificationFilter(StrEnum):     # notifications.ts:14
    unread = "unread"
    all = "all"
    errors = "errors"

class SampleStatus(StrEnum):           # onboarding.ts:10
    received = "received"
    analyzing = "analyzing"
    ready = "ready"
    failed = "failed"

CHAIN_DEPTH_CAP = 3                    # quarantine.ts:82; architecture §6.5

# Mapping Review override dropdown vocabulary (onboarding.ts:72). Names are
# authoritative dis-canonical columns; the subset is the curated Phase-1 set.
CANONICAL_COLUMNS = (
    "sku_id", "store_id", "quantity", "unit_sale_price", "unit_retail_price",
    "current_retail_price", "source_sale_timestamp", "transaction_id",
    "product_name", "product_description", "currency", "tax_treatment",
)

# ---- sources (sources.ts) ---------------------------------------------------

class Source(BaseModel):               # sources.ts:11
    source_id: str                     # kind-style key; immutable once created
    name: str
    type: str                          # UI vocabulary: "CSV" | "JSON" | "API" (sources.ts:35, provisional)
    store: str
    status: SourceStatus
    active_version: int                # version_seq_per_source of the ACTIVE mapping; 0 if none
    quarantine_rate_24h: float
    last_ok_at: str                    # ISO 8601

class SourceCreate(BaseModel):         # sources.ts:27 SourceDraft (request)
    source_id: str = Field(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=128)
    name: str = Field(min_length=1)
    type: str
    store: str

class SourcePatch(BaseModel):          # sources.ts updateSource: Pick<SourceDraft,'name'|'type'|'store'>
    name: str | None = None            # source_id is NEVER patchable (FM4)
    type: str | None = None
    store: str | None = None

# ---- dashboard (dashboard.ts) -----------------------------------------------

class DashboardSource(BaseModel):      # dashboard.ts:15
    source_id: str
    name: str
    source_type: str                   # identity key: csv | shopify_pos | square | other
    health: SourceHealth
    rows_24h: int
    last_ok_at: str
    quarantined_open: int

class LatencySnapshot(BaseModel):      # dashboard.ts:28
    p50_ms: int
    p95_ms: int
    p99_ms: int

class DashboardSummary(BaseModel):     # dashboard.ts:34
    tenant_id: str
    sources: list[DashboardSource]
    latency_1h: LatencySnapshot

# ---- mappings (mappings.ts; DB: config.source_mappings) ---------------------

class MappingRules(BaseModel):         # mappings.ts:28 — the UI's flat view
    rename: dict[str, str]             # source_col -> canonical_col
    normalize: dict[str, str]          # canonical_col -> format label (UI renders strings)
    cast: dict[str, str]               # canonical_col -> type label
    derive: dict[str, str]
    # NOTE: the DB mapping_rules JSONB (D49) is richer — normalize is
    # dict[str, list[{op, ...args}]], cast is dict[str, {type, precision?, scale?}],
    # plus a "version" key. The BFF DOWN-RENDERS to the UI's flat string view for
    # 3.2 reads (e.g. normalize op list -> "parse_date DD-MM-YYYY"). The full D49
    # document is what gets WRITTEN on approve (§5.2); the UI never round-trips
    # mapping_rules, so no information is lost. PROVISIONAL until the UI grows a
    # structured rules editor.

class MappingVersion(BaseModel):       # mappings.ts:13
    version: int                       # = version_seq_per_source
    status: MappingStatus
    created_at: str
    created_by: str                    # display name; DB has created_by_user_id UUID — Blocker 5
    field_count: int                   # len(mapping_rules.rename)
    transform_count: int               # count of normalize+cast+derive entries
    suite_version: int                 # PROVISIONAL: no suite-version column exists; derive from
                                       # pre/post_validation_suite_ref presence or echo version
    active_from: str | None            # = activated_at
    active_to: str | None              # = deprecated_at (null while active)

class MappingVersionDetail(MappingVersion):  # mappings.ts:35
    mapping_rules: MappingRules

# ---- onboarding (onboarding.ts; LLM contract: docs/slices/llm-mapping-suggestion-contract.md)

class SampleTransform(BaseModel):      # onboarding.ts:12
    type: str                          # e.g. "date_format"
    value: str                         # e.g. "DD-MM-YY"

class SuggestionAlternative(BaseModel):  # onboarding.ts:16
    target: str                        # must be from CANONICAL_COLUMNS
    confidence: float = Field(ge=0.0, le=1.0)

class SampleColumn(BaseModel):         # onboarding.ts:18
    source_col: str
    inferred_type: str                 # DuckDB-inferred
    sample_values: list[str]
    null_pct: float
    proposed_canonical: str
    confidence: float = Field(ge=0.0, le=1.0)
    transforms: list[SampleTransform]
    # LLM-shaped assist fields (R8). OPTIONAL on the wire; mechanical inferences
    # omit them; the UI degrades gracefully and never fabricates them.
    reasoning: str | None = None
    alternatives: list[SuggestionAlternative] | None = None

class SampleAnalysis(BaseModel):       # onboarding.ts:35
    sample_id: str
    status: SampleStatus
    columns: list[SampleColumn]        # empty until status == "ready"

class CreateSampleResult(BaseModel):   # onboarding.ts:46 (RECOVERED shape)
    sample_id: str
    gcs_uri: str
    status: Literal["received"]

class ColumnOverride(BaseModel):       # onboarding.ts:49 (request AND response — echoed)
    source_col: str
    proposed_canonical: str | None = None
    transforms: list[SampleTransform] | None = None
    authoritative: bool | None = None

class DryRunResult(BaseModel):         # onboarding.ts:59
    rows: list[dict[str, Any]]         # 10-20 canonical-keyed rows

class ApproveResult(BaseModel):        # onboarding.ts:62
    source_id: str
    mapping_version: int
    status: Literal["staged"]

# ---- shadow (shadow.ts) ------------------------------------------------------

class ShadowStats(BaseModel):          # shadow.ts:29
    source_id: str
    staged_version: int
    active_version: int | None         # null on first onboarding
    window: str                        # e.g. "last 48h"
    input_chunks: int
    staged_rows: int
    validation_pass_rate: float        # 0..1
    validation_fail_count: int
    diff_identical: int
    diff_differing: int
    diff_column: str                   # most-differing canonical column

class ShadowDiffRow(BaseModel):        # shadow.ts:44
    sku_id: str
    column: str
    active_value: str
    staged_value: str

class PromoteResult(BaseModel):        # shadow.ts:52
    source_id: str
    promoted_version: int
    deprecated_version: int | None
    status: Literal["promoted"]

class RejectResult(BaseModel):         # shadow.ts:59
    source_id: str
    rejected_version: int
    status: Literal["rejected"]

# ---- quarantine (quarantine.ts; DB: quarantine.quarantined_rows) -------------

class QuarantineRow(BaseModel):        # quarantine.ts:17
    trace_id: str                      # UUIDv7
    source_id: str                     # kind-style key (filter key; Dashboard ?source= carries it)
    source: str                        # display name (registry join — Blocker 2; fallback: source_id)
    store: str                         # display name via identity_mirror.stores.name
    error_reason: str                  # = failure_reason
    failure_stage: FailureStage        # translated per §2.6
    mapping_version: int               # = version_seq_per_source of mapping_version_id
    failed_at: str                     # = quarantined_at
    status: QuarantineStatus           # translated per §2.6

class FleetQuarantineRow(QuarantineRow):  # ops-cross-tenant.ts:27
    tenant_id: str
    tenant_name: str                   # identity_mirror.tenants.name

class ResubmitRecord(BaseModel):       # quarantine.ts:74
    child_trace_id: str
    resubmit_type: ResubmitType
    chain_depth: int

class QuarantineDetail(BaseModel):     # quarantine.ts:34
    trace_id: str
    source: str
    store: str
    failed_at: str
    failure_stage: FailureStage
    mapping_version: int
    error_reason: str
    error_context: str                 # rendered from failure_context JSONB
    original_payload: dict[str, Any]   # fetched from GCS (gcs_uri + row_offset) — see §4.2
    chain_depth: int                   # Blocker 4
    resubmits: list[ResubmitRecord]    # Blocker 4

class ResubmitRequest(BaseModel):      # quarantine.ts:55 — PINNED by demand list 4.3
    resubmit_type: ResubmitType
    parent_trace_id: str
    tenant_id: str | None = None       # ops-cross-tenant.ts:33 adds it; tenant callers omit it.
                                       # Tenant callers: server IGNORES it (token wins). Ops callers: REQUIRED.

class ResubmitResponse(BaseModel):     # quarantine.ts:65
    trace_id: str                      # minted child trace (UUIDv7)
    parent_trace_id: str
    resubmit_type: ResubmitType
    chain_depth: int                   # depth AFTER this resubmit
    status: Literal["accepted"]

# ---- audit (audit.ts; DB: audit.events) --------------------------------------

class AuditStage(BaseModel):           # audit.ts:20
    stage: str                         # UI lifecycle vocabulary (§2.6)
    at: str
    status: str                        # "ok" | "error"
    mapping_version_id: int | None = None   # serialized ABSENT when null (TS optional `?`)
    error_code: str | None = None           # = failure_code; ABSENT when null

class AuditTrace(BaseModel):           # audit.ts:28
    trace_id: str
    tenant_id: str | None
    source_id: str                     # Blocker 7
    stages: list[AuditStage]           # ordered by event_timestamp
    prior_trace_id: str | None         # from event_data (D42) — Blocker 4 adjacency

class CrossTenantAuditTrace(AuditTrace):  # ops-cross-tenant.ts:36
    tenant_name: str

# ---- notifications (notifications.ts) — Blocker 1 ----------------------------

class Notification(BaseModel):         # notifications.ts:19
    id: str
    severity: Severity
    text: str
    source: str                        # source display name
    at: str
    read: bool
    link: str                          # in-app path; UI does not navigate on it yet

class UnreadCount(BaseModel):          # notifications.ts:29
    unread: int

# ---- ops fleet (ops-fleet.ts) -------------------------------------------------

class FleetSummary(BaseModel):         # ops-fleet.ts:27
    tenant_count: int
    healthy: int
    warning: int
    failing: int
    total_rows_24h: int
    open_quarantine: int

class FleetTenant(BaseModel):          # ops-fleet.ts:38
    tenant_id: str
    name: str                          # identity_mirror.tenants.name
    health: SourceHealth               # FleetHealth ≡ SourceHealth
    rows_24h: int
    open_quarantine: int
    last_activity_at: str

# ---- ops duckdb (ops-query.ts) — Blocker 3 ------------------------------------

class QueryRequest(BaseModel):         # ops-query.ts:18 — frontend sends sql ONLY
    sql: str = Field(min_length=1)

class QueryColumn(BaseModel):          # ops-query.ts:22
    name: str
    type: str

class QueryResult(BaseModel):          # ops-query.ts:23
    columns: list[QueryColumn]
    rows: list[list[Any]]              # tuples aligned to columns

# ---- me (types.ts) — Blocker 5 -------------------------------------------------

class MeResponse(BaseModel):           # types.ts:10
    user_id: str
    email: str
    name: str
    tenant_id: str | None
    tenant_name: str | None            # identity_mirror.tenants.name; null for ops

# ---- csv uploads (Slice 8, synchronous; supersedes the D36 signed-URL design) ---
# Request is multipart/form-data, not a JSON model: parts `file` (binary CSV,
# 10 MB cap enforced MID-STREAM), `template_id` (UUID text), `store_code` (text).
# Unknown parts are drained and ignored (tenant comes from the token ONLY).

class CsvUploadResult(BaseModel):      # 201 — schemas/csv_uploads.py (live)
    trace_id: UUID                     # UUIDv7, minted HERE (hard rule 4 origin point)
    upload_id: str                     # ^us_[a-z0-9]{12}$ — deterministic per logical
                                       # upload (csv.received upload_session_id; the
                                       # worker's D58 idempotency component)
    tenant_id: UUID                    # resolved internal UUIDs (D37/D52)
    store_id: UUID
    store_code: str
    source_id: str                     # derived from the template lineage, never the request
    template_id: UUID
    gcs_uri: str                       # the D53 path the object was written to
    row_count: int                     # tier-0 observed data rows (excl. header)
    received_ts: datetime
    status: Literal["received"]
```

**Optional-field serialization rule.** TS optional fields (`?`) — `AuditStage.mapping_version_id`, `AuditStage.error_code`, `SampleColumn.reasoning`, `SampleColumn.alternatives` — are serialized with `exclude_none=True` semantics on those fields (absent, not `null`). TS `| null` fields (`active_from`, `tenant_id` on `AuditTrace`, etc.) serialize as explicit `null`. Configure response models accordingly.

---

## 5. Endpoints

Format per endpoint: path · screen/call-site trace · params · request · response · status codes · side effects. **Canonical write: no** holds for every endpoint in this document; it is restated only where a reader might suspect otherwise.

### Group 1 — Cross-cutting

#### 1.1 GET /v1/me
- **Call site:** `AppLayout.tsx` on every authenticated mount, via `me.ts: getMe` / `useMe`. Renders email + avatar in the header.
- **Auth:** any valid token (tenant or ops).
- **Response 200:** `MeResponse`.
- **Errors:** 401 `AuthTokenError`.
- **Side effects:** none. Reads `identity_mirror.tenants.name` for `tenant_name` (via dis-rls session). `email`/`name`: Customer Master profile call — **Blocker 5**; the dev stub serves deterministic placeholder values derived from `sub` until the CM contract lands, clearly marked in code.

#### 1.2 GET /v1/dashboard/summary
- **Call site:** `Dashboard.tsx` on mount, `dashboard.ts: getDashboardSummary` / `useDashboardSummary`.
- **Auth:** tenant (`require_tenant`).
- **Response 200:** `DashboardSummary`, or **`null`** (nullable-lookup rule) when the tenant has no sources/activity — the UI renders an EmptyState.
- **Errors:** 401, 403 `TenantScopeError`.
- **Side effects:** none. **Reads (all under `rls_session(tenant_id)`):** sources registry (Blocker 2) for the source list; `audit.events` for `rows_24h` (sum of `rows_succeeded` on `CANONICAL_WRITTEN`/`SUCCESS` in 24 h, per source from `event_data.source_id` — Blocker 7), `last_ok_at` (max `event_timestamp` of a success), and `latency_1h` (percentiles over `duration_ms`); `quarantine.quarantined_rows` for `quarantined_open` (count `status='NEW'` per source). `health` classification: **Blocker 6**.

#### 1.3 GET /v1/sources
- **Call sites:** `SourcesIndex.tsx` (table), `SampleUpload.tsx` (attach-to-existing select), Dashboard context — all via `sources.ts: getSources` / `useSources`.
- **Auth:** tenant. Ops may pass `?tenant_id=` for cross-tenant (demand list 1.3); the current UI never does — additive.
- **Query:** `status: SourceStatus` (optional), `q: str` (optional name search), `tenant_id` (ops only), `limit`/`offset` (§2.5).
- **Response 200:** `list[Source]`, ordered by `name` asc. Empty array for a tenant with no sources (frontend expects `[]`, not null).
- **Errors:** 401, 403.
- **Side effects:** none. Reads the source registry (**Blocker 2**) joined with `config.source_mappings` (active version via the `uq_csm_active_per_source` partial index), quarantine counts, audit recency.

#### 1.4 GET /v1/sources/{source_id}
- **Call site:** `SourceEdit.tsx` on mount, `sources.ts: getSource` / `useSource`.
- **Auth:** tenant.
- **Path:** `source_id: str`.
- **Response 200:** `Source`, or **`null`** for unknown source (nullable-lookup rule; the UI renders "not found" EmptyState).
- **Errors:** 401, 403.
- **Side effects:** none. Same reads as 1.3, single row.

#### 1.5 POST /v1/sources
- **Call site:** `SourceCreate.tsx` submit, `sources.ts: createSource` / `useCreateSource`. UI-defined endpoint (slice 27); not in the demand list.
- **Auth:** tenant.
- **Request:** `SourceCreate`. `source_id` is operator-set (kind-style slug, derived client-side from name via `deriveSourceId`) and **immutable forever after** (FM4).
- **Response 201:** `Source` — the created record with `status="active"`, `active_version=0`, `quarantine_rate_24h=0.0`, `last_ok_at` = creation time (fixture parity: `sources.ts createSource`).
- **Errors:** 401, 403; 409 `MappingStateConflictError` if `source_id` already exists for the tenant; 422 body validation.
- **Side effects:** writes the source registry — **Blocker 2** (no table exists; implementation blocked until the registry decision). Audit emission (fire-and-forget) once a stage vocabulary covers config changes (§9).

#### 1.6 PATCH /v1/sources/{source_id}
- **Call site:** `SourceEdit.tsx` submit, `sources.ts: updateSource` / `useUpdateSource`.
- **Auth:** tenant.
- **Request:** `SourcePatch` (display metadata only; any attempt to alter `source_id` is impossible by shape).
- **Response 200:** updated `Source`.
- **Errors:** 401, 403; 404 `ResourceNotFoundError` (the edit screen loaded it, so a 404 here means it vanished — throw-style is correct; the mutation surfaces an error state).
- **Side effects:** registry write — **Blocker 2**.

#### 1.7 POST /v1/sources/{source_id}/deprecate
- **Call site:** `SourcesIndex.tsx` confirm dialog, `sources.ts: deprecateSource` / `useDeprecateSource`. Verb/path per demand list 1.6.
- **Auth:** tenant.
- **Request:** empty body.
- **Response 200:** updated `Source` with `status="deprecated"`. Soft transition only; **no hard delete exists anywhere in this surface**.
- **Errors:** 401, 403, 404.
- **Side effects:** registry write — **Blocker 2**. Note: deprecating a source does not touch mapping rows; new submissions are rejected at receivers (out of this service's scope).

### Group 2 — Onboarding and shadow rollout

Onboarding state persistence (within this service's write scope): the sample's draft mapping lives as a `config.source_mappings` row with `status='DRAFT'` — a vocabulary member that exists in the DDL precisely for onboarding ("default mapping seeded by onboarding service", schema comment) and is never surfaced in version lists (§2.6). Sample bytes live in GCS under an onboarding-staging path (dis-storage extension, §9). `sample_id` (`smp_` + suffix) maps 1:1 to the DRAFT row; analysis metadata (inferred columns, status) is held in that row's `metadata` JSONB. DRAFT rows are invisible to §3 reads.

#### 2.1 POST /v1/onboarding/samples  (RECOVERED)
- **Call site:** `SampleUpload.tsx` "Analyze sample", `onboarding.ts: createSample`.
- **Auth:** tenant or ops (`tenant_id` form field, ops-on-behalf — demand list 2.1).
- **Request:** `multipart/form-data` — `file` (binary, required, `.csv`, 10 MB cap per surface map §3), `source_kind` (required; e.g. `csv`, `json`), `label` (required), `tenant_id` (optional, ops only). The UI's "attach to existing source" choice is client-side only (demand list 2.1 carries no source-instance ref); the source linkage happens at approve (2.5).
- **Response 201:** `CreateSampleResult` — `{sample_id, gcs_uri, status: "received"}`.
- **Errors:** 401, 403; 400 `UploadSessionError` (malformed/empty/over-cap sample — tier-0 structural checks per D51 live here, where the bytes actually arrive); 502 `StorageError`.
- **Side effects:** GCS write (onboarding-staging path via dis-storage, §9); creates the DRAFT `config.source_mappings` row under `rls_session(tenant_id)`; kicks in-process analysis (DuckDB inference → suggestion → validation draft, per README `sample_upload` EPE). Audit emission fire-and-forget.

#### 2.2 GET /v1/onboarding/samples/{sample_id}  (RECOVERED)
- **Call sites:** `SampleUpload.tsx` (poll until `ready`/`failed`, then auto-navigate), `MappingReview.tsx` (load) — `onboarding.ts: getSample` / `useSample`.
- **Auth:** tenant or ops.
- **Response 200:** `SampleAnalysis`. `columns` populated when `status="ready"`; `reasoning`/`alternatives` present only where the suggestion layer produced them (LLM contract: optional, never fabricated; `suggested_target`/targets drawn from `CANONICAL_COLUMNS`).
- **Errors:** 401, 403; 404 `ResourceNotFoundError` (frontend throws for unknown sample); 422 if inference cannot proceed maps instead to `status="failed"` in the body (the UI's failure state keys off `status`, not HTTP).
- **Side effects:** none.

#### 2.3 PATCH /v1/onboarding/samples/{sample_id}/mapping
- **Call site:** `MappingReview.tsx` per-column override, `onboarding.ts: patchSampleMapping`.
- **Auth:** tenant or ops.
- **Request:** `ColumnOverride` (single column, partial).
- **Response 200:** the same `ColumnOverride`, echoed (fixture-pinned contract parity; the screen keeps the authoritative draft in local state).
- **Errors:** 401, 403, 404; 400 `MappingConfigError` if `proposed_canonical` is not in `CANONICAL_COLUMNS`.
- **Side effects:** merges the override into the DRAFT row's `mapping_rules`/`metadata` under `rls_session`.

#### 2.4 POST /v1/onboarding/samples/{sample_id}/dry-run
- **Call site:** `MappingReview.tsx` "Continue to preview", `onboarding.ts: dryRunSample`.
- **Auth:** tenant or ops.
- **Request:** empty body (the draft mapping on the server is the input).
- **Response 200:** `DryRunResult` — 10–20 rows rendered by executing the current draft mapping against the stored sample via `libs/dis-mapping` (the same engine the streaming consumer uses; one rule vocabulary, D49). Row keys are the proposed canonical columns.
- **Errors:** 401, 403, 404; 422 `MappingInputError` (sample violates engine contract); 400 `MappingConfigError` (draft rules invalid); 500 `ValidationSuiteError`.
- **Side effects:** none (pure render; no persistence).

#### 2.5 POST /v1/onboarding/samples/{sample_id}/approve
- **Call site:** `MappingReview.tsx` "Go live" step, `onboarding.ts: approveSample`.
- **Auth:** tenant or ops.
- **Request:** empty body.
- **Response 200:** `ApproveResult` — `{source_id, mapping_version, status: "staged"}` where `mapping_version` is the new row's `version_seq_per_source` (trigger-assigned).
- **Errors:** 401, 403, 404; 409 `MappingStateConflictError` if a STAGED version already exists for the source (one staged at a time — promote/reject semantics assume a single staged version).
- **Side effects:** transitions the DRAFT row `DRAFT → STAGED` (writes the full D49 `mapping_rules` document with mandatory locale declarations confirmed by the operator); stamps `created_by_user_id` from the token `sub`; publishes **`mapping.changed`** (`event_type="created"`, `status="staged"`) for streaming-consumer awareness; audit emission fire-and-forget. Starts the shadow-rollout window by convention (the staged row's existence is the signal; execution is the streaming consumer's — Blocker 8).

#### 2.6 GET /v1/sources/{source_id}/shadow-stats
- **Call site:** `Shadow.tsx` on mount, `shadow.ts: getShadowStats` / `useShadowStats`.
- **Auth:** tenant.
- **Response 200:** `ShadowStats`, or **`null`** (nullable-lookup rule) when no STAGED version exists or no rollup data has accumulated — the UI renders "no staged version" EmptyState.
- **Errors:** 401, 403.
- **Side effects:** none. **Reads:** `config.source_mappings` (staged/active `version_seq_per_source`), `staging.*` row counts, `audit.events` for pass/fail counts on staged-run validation — **Blocker 8** for the bookkeeping that fills `window`/`input_chunks`/`validation_pass_rate`.

#### 2.7 GET /v1/sources/{source_id}/shadow-diff
- **Call site:** `Shadow.tsx` on mount, `shadow.ts: getShadowDiff` / `useShadowDiff`.
- **Auth:** tenant.
- **Query:** `limit: int = 10` (demand list 2.7; cap 100).
- **Response 200:** `list[ShadowDiffRow]`. Empty array when no staged version or no active baseline (first onboarding) — frontend expects `[]`.
- **Errors:** 401, 403.
- **Side effects:** none. Diff computed by the in-process `onboarding/shadow/compare.py` over `staging.*` vs `canonical.*` reads (read replica; both via dis-rls).

#### 2.8 POST /v1/sources/{source_id}/promote
- **Call site:** `Shadow.tsx` "Promote to active", `shadow.ts: promoteShadow` / `usePromoteShadow`.
- **Auth:** tenant.
- **Request:** empty body.
- **Response 200:** `PromoteResult` — `{source_id, promoted_version, deprecated_version (null if no prior active), status: "promoted"}`.
- **Errors:** 401, 403; 409 `MappingStateConflictError` (no staged version — frontend fixture throws here too).
- **Side effects:** single `rls_session` transaction: STAGED row → `ACTIVE` (+`activated_at`), prior ACTIVE row (if any) → `DEPRECATED` (+`deprecated_at`) — the `uq_csm_active_per_source` partial unique index makes the swap race-safe. Publishes **`mapping.changed`** (`event_type="promoted"`, `previous_active_version_id` set). Audit fire-and-forget. **Writes config only; the streaming consumer picks up the new active mapping via the `mapping.changed` side-input refresh — no canonical write here.**

#### 2.9 POST /v1/sources/{source_id}/reject
- **Call site:** `Shadow.tsx` "Reject, iterate", `shadow.ts: rejectShadow` / `useRejectShadow`.
- **Auth:** tenant.
- **Request:** empty body.
- **Response 200:** `RejectResult` — `{source_id, rejected_version, status: "rejected"}`. Active version untouched.
- **Errors:** 401, 403; 409 `MappingStateConflictError` (no staged version).
- **Side effects:** STAGED → `DEPRECATED` (+`deprecated_at`); publishes **`mapping.changed`** (`event_type="deprecated"`); audit fire-and-forget.

### Group 3 — Mapping versions (read-only in this surface)

The UI's Mapping Versions screen is read-only ("New version (Phase 2)" button is disabled). Demand-list 3.3/3.4 (create version, ops deprecate) have no call sites — see Appendix A.

#### 3.1 GET /v1/sources/{source_id}/mappings
- **Call site:** `MappingVersions.tsx` on mount, `mappings.ts: getMappingVersions` / `useMappingVersions`.
- **Auth:** tenant.
- **Response 200:** `list[MappingVersion]`, ordered `version` desc. Empty array for unknown source (frontend expects `[]`). DRAFT rows excluded.
- **Errors:** 401, 403.
- **Side effects:** none. Reads `config.source_mappings` under `rls_session`; field mappings as annotated on the model (`version`=`version_seq_per_source`, `active_from`=`activated_at`, `active_to`=`deprecated_at`, counts derived from `mapping_rules`). `created_by`: **Blocker 5**.

#### 3.2 GET /v1/sources/{source_id}/mappings/{version}
- **Call site:** `MappingVersions.tsx` "View", `mappings.ts: getMappingVersion` / `useMappingVersion`.
- **Auth:** tenant.
- **Path:** `version: int` (the per-source sequence number, not the global BIGSERIAL).
- **Response 200:** `MappingVersionDetail`, or **`null`** for unknown version (nullable-lookup rule).
- **Errors:** 401, 403.
- **Side effects:** none. `mapping_rules` down-rendered from the D49 JSONB to the UI's flat view (see `MappingRules` model note).

### Group 4 — Quarantine

#### 4.1 GET /v1/quarantine
- **Call sites:** tenant — `QuarantineConsole.tsx` via `quarantine.ts: getQuarantine` / `useQuarantine`; ops — same screen at `/ops/quarantine` via `ops-cross-tenant.ts: getFleetQuarantine` / `useFleetQuarantine`.
- **Auth:** tenant; ops reads cross-tenant (no `tenant_id` claim, `dis:ops` role).
- **Query (all optional; UI currently filters client-side):** `source_id`, `failure_stage: FailureStage`, `status: QuarantineStatus`, `from`/`to` (ISO 8601), `tenant_id` (ops only), `limit` (default 500)/`offset`.
- **Response 200:** tenant callers get `list[QuarantineRow]`; ops callers get `list[FleetQuarantineRow]` (same rows + `tenant_id`, `tenant_name`). One endpoint, response enriched when the caller is ops — matching the two frontend modules. Ordered `failed_at` desc.
- **Errors:** 401, 403.
- **Side effects:** none. **Reads:** `quarantine.quarantined_rows` (tenant: `rls_session(tenant_id)`; ops: platform read path §2.2); enum translations §2.6; `store` display via `identity_mirror.stores.name`; `source` display — Blocker 2 fallback `source_id`; `mapping_version` resolved `mapping_version_id` → `version_seq_per_source`.

#### 4.2 GET /v1/quarantine/{trace_id}
- **Call sites:** `quarantine.ts: getQuarantineRow` / `useQuarantineRow` (tenant), `ops-cross-tenant.ts: getFleetQuarantineRow` (ops). Both render the same detail panel.
- **Auth:** tenant (RLS-scoped — a cross-tenant trace is a 404, indistinguishable from absent); ops cross-tenant.
- **Response 200:** `QuarantineDetail`.
- **Errors:** 401, 403; **404** `ResourceNotFoundError` (frontend throws for unknown trace — throw-style, not nullable).
- **Side effects:** none. **Reads:** the quarantine row; `original_payload` is **not stored in Postgres by design** (PII posture) — the handler fetches the chunk from GCS via dis-storage (`split_object_uri(gcs_uri)` + `StorageClient.download_blob`) and extracts the row at `row_offset`, verifying `row_sha256` when present; `error_context` rendered from `failure_context` JSONB. `chain_depth`/`resubmits`: **Blocker 4** — until resolved, serve `chain_depth=0`, `resubmits=[]` (the UI degrades gracefully; cap enforcement in 4.3 still applies via whatever lineage source the blocker decision lands).

#### 4.3 POST /v1/quarantine/{trace_id}/resubmit
- **Call sites:** `quarantine.ts: postResubmit` / `useResubmit` (tenant), `ops-cross-tenant.ts: postOpsResubmit` / `useOpsResubmit` (ops; body adds `tenant_id`).
- **Auth:** tenant; ops cross-tenant (then body `tenant_id` is **required** and names the acted-on tenant; for tenant callers the token wins and a body `tenant_id` is ignored).
- **Path:** `trace_id` — must equal body `parent_trace_id`; a mismatch is a 422 body-validation failure.
- **Request:** `ResubmitRequest`.
- **Response 202:** `ResubmitResponse` — child `trace_id` minted here (UUIDv7; this is an ingress-starting action, an allowed mint point alongside receivers), `chain_depth` = parent depth + 1.
- **Errors:** 401, 403, 404 (unknown parent trace); **409 `ResubmitChainCapError`** when parent `chain_depth >= 3` (architecture §6.5 — backend-enforced; the UI also disables the button at cap); 422.
- **Side effects:** publishes **`ingress.resubmit`** (frozen contract: UUID identity + codes, `trace_id` = child, `parent_trace_id`, `replay=true` semantics; consumed by the streaming consumer). Records the resubmit in the lineage store (**Blocker 4**). Audit fire-and-forget. `fixed_file` body gap: **Blocker 9**.

### Group 5 — Audit

#### 5.1 GET /v1/audit/{trace_id}
- **Call sites:** `AuditLookup.tsx` form submit — tenant via `audit.ts: getAuditTrace` / `useAuditTrace`; ops at `/ops/audit` via `ops-cross-tenant.ts: getCrossTenantAuditTrace` / `useCrossTenantAuditTrace`.
- **Auth:** tenant (own-tenant only); ops cross-tenant.
- **Response 200:** tenant: `AuditTrace` or **`null`** (nullable-lookup rule — unknown trace AND cross-tenant trace both serve `null`; the UI renders "not found", and `null` avoids existence-leaking across tenants). Ops: `CrossTenantAuditTrace` or `null` (adds `tenant_name` from `identity_mirror.tenants`).
- **Errors:** 401, 403.
- **Side effects:** none. **Reads:** `audit.events` WHERE `trace_id = :id` ordered by `event_timestamp` (Phase 1 Cloud SQL per D34; the repo interface is stable for the Phase-3 BigQuery augmentation). Stage/outcome translation per §2.6; `mapping_version_id` and `error_code` (= `failure_code`) attached per stage where present; `source_id` from `event_data` — **Blocker 7**; `prior_trace_id` from `event_data` (D42) — absent → `null`.

### Group 6 — Notifications  (all four: **Blocker 1** — shapes pinned, persistence undecided)

#### 6.1 GET /v1/notifications
- **Call site:** `Notifications.tsx`, `notifications.ts: getNotifications` / `useNotifications`.
- **Auth:** tenant.
- **Query:** `filter: NotificationFilter = "all"` (`unread` | `all` | `errors`; `errors` = `severity == "error"`).
- **Response 200:** `list[Notification]`, ordered `at` desc, cap 200.
- **Errors:** 401, 403.

#### 6.2 GET /v1/notifications/unread-count
- **Call site:** `NotificationBell.tsx` (header, all screens), 30 s poll — `notifications.ts: getUnreadCount` / `useUnreadCount`.
- **Auth:** tenant.
- **Response 200:** `UnreadCount`. `Cache-Control: no-store`.
- **Errors:** 401, 403.

#### 6.3 PATCH /v1/notifications/{id}/read
- **Call site:** `Notifications.tsx` per-row "Mark read", `notifications.ts: markNotificationRead` / `useMarkRead`.
- **Auth:** tenant.
- **Request:** empty body. **Response 204:** no content (frontend type is `Promise<void>`; `client.ts` calls `response.json()` — slice-13 wiring must tolerate empty bodies for the two mark-read calls; flagged in Appendix B note, not a shape change). Unknown id: **204 anyway** (frontend pins no-op semantics for not-found).
- **Errors:** 401, 403.
- **Side effects:** sets `read = true`.

#### 6.4 POST /v1/notifications/mark-all-read
- **Call site:** `Notifications.tsx` header button, `notifications.ts: markAllNotificationsRead` / `useMarkAllRead`.
- **Auth:** tenant. **Request:** empty. **Response 204.**
- **Errors:** 401, 403.
- **Side effects:** sets `read = true` for all of the tenant's notifications.

### Group 7 — Ops

#### 7.1 GET /v1/ops/fleet/summary
- **Call site:** `OpsFleet.tsx` on mount, `ops-fleet.ts: getFleetSummary` / `useFleetSummary`.
- **Auth:** ops (`require_ops`).
- **Response 200:** `FleetSummary`.
- **Errors:** 401, 403 `OpsRoleRequiredError`.
- **Side effects:** none. **Reads (platform scope):** `identity_mirror.tenants` count; per-tenant health rollup (**Blocker 6**); `audit.events` 24 h row sums; `quarantine.quarantined_rows` open counts.

#### 7.2 GET /v1/ops/fleet/tenants
- **Call site:** `OpsFleet.tsx` on mount, `ops-fleet.ts: getFleetTenants` / `useFleetTenants`.
- **Auth:** ops.
- **Query (additive, demand list 7.2):** `sort` (default `open_quarantine` desc), `limit`/`offset`.
- **Response 200:** `list[FleetTenant]`. `name` from `identity_mirror.tenants.name`; `last_activity_at` = latest `audit.events.event_timestamp` per tenant; `health`: **Blocker 6**.
- **Errors:** 401, 403.

#### 7.3 POST /v1/ops/duckdb/query  (**Blocker 3** — shape pinned to frontend; execution contract undecided)
- **Call site:** `OpsQuery.tsx` "Run", `ops-query.ts: executeQuery` / `useRunQuery`.
- **Auth:** ops. RBAC at the handler (security-sensitive surface; `duckdb_runner/` isolation per README).
- **Request:** `QueryRequest` — `{sql}` only (frontend-pinned; the demand-list `gcs_uri` field is the Blocker-3 conflict).
- **Response 200:** `QueryResult`. Empty result: `{columns: [...], rows: []}`.
- **Errors:** 401, 403; **400 `DuckDbQueryError`** — the envelope `message` is the DuckDB error text (the UI shows its own generic error state today; the message is for the error envelope/logs); **504 `DuckDbTimeoutError`** (configurable cap, README).
- **Side effects:** none persisted. In-process DuckDB over GCS bronze objects via dis-storage-resolved URIs; read-only enforcement, row caps, and the `bronze` table-resolution model await Blocker 3.

### Group 8 — CSV upload (Slice 8, BUILT — synchronous; supersedes the signed-URL design; **no dis-ui call site yet**)

The UI has no real-data-upload journey yet (`/upload` is onboarding samples, a different flow); the missing UI journey is recorded here as a gap for a future dis-ui slice, not a blocker. **Supersession (register entry at the Slice 8 commit gate):** the original 8.1/8.2 (signed PUT URL + upload-session object + confirm) is REMOVED — with a 10 MB ceiling there is no large-file case for direct-to-GCS, so the bytes stream through the server in one request, which also closes D54's open "how does the server learn the PUT completed" fork (no detection exists to need). D36's *placement* (Phase 1 inside dis-ui-server) and D54's *trust model* (the worker reads identity off `csv.received` and resolves nothing) stand unchanged.

#### 8.1 POST /v1/csv-uploads  (live: `handlers/csv_uploads.py`)
- **Mandate:** D36 (placement) + the Slice 8 supersession; D51/D52 (tier-0 here); D71 (`template_id` carried end to end, consumer unamended until Slice 8a).
- **Auth:** tenant (requires `tenant_id`; `user_id` from `sub`). Tenant from the TOKEN only; a smuggled body `tenant_id` is drained and ignored (test-pinned).
- **Request:** `multipart/form-data` — `file` (binary CSV), `template_id` (UUID), `store_code` (text). **10 MB cap enforced mid-stream** (`upload_stream.py`: the Content-Length early-reject is the spoofable first check; the streaming byte counter is the real boundary — the body is never fully read past the ceiling).
- **Response 201:** `CsvUploadResult` — the only place in this service (with 4.3) that **mints `trace_id`** (UUIDv7 via dis-core, bound to the request context so every error envelope carries it).
- **Errors:** 401, 403; **413 `PayloadTooLargeError`** (mid-stream or declared); **400 `UploadRequestError`** (not multipart / missing or repeated part / malformed `template_id`; values never echoed); **422 `UploadStructureError(reason)`** (tier-0: `empty_file` / `not_utf8` / `not_csv` / `below_min_rows` — no GCS write, no publish); **404 `ResourceNotFoundError`** (unknown/cross-tenant `template_id` or `store_code` — RLS/in-query scoping; no existence oracle); **409 `MappingStateConflictError`** (template has no ACTIVE version); **409 `StoreStateConflictError`** (store resolved but not ACTIVE — the gate runs AFTER the 404 resolve, so a cross-tenant code stays 404); **503 `StorageError`** (GCS write failed; nothing published); **503 `EventPublishError`** (publish failed AFTER the object was written — the object is an accepted orphan, deliberately not deleted; a retry of the same bytes converges via the deterministic `upload_id`).
- **Side effects (order is load-bearing):** stream+limit → tier-0 (D51) → template resolve via `rls_session` (`resolve_active_template`; the ACTIVE row supplies `source_id`) → store resolve via the in-query mirror chokepoint (`resolve_store_by_code`) → ACTIVE-only store gate → GCS write at `build_object_path(tenant_uuid, source_id, trace_id, (Y,M,D), "csv")` → **`csv.received` publish** (frozen contract incl. required `template_id`; `upload_session_id` = deterministic `us_` + 12-hex of SHA-256 over `tenant|store|template|payload_sha256`, so client retries collapse in the worker's D58 dedup) → audit (fire-and-forget, `Stage.RECEIVED` + `event_data.phase="csv_upload_phase1"`; the dedicated-stage gap remains §9/D42). Phase 2 (`csv-ingest-worker`) takes over from the publish (D54). No bronze write here; no upload-session record exists anymore.

---

## 6. Open dependencies

What this service needs from elsewhere, and how each is stubbed until it lands:

| Dependency | Needed by | Status / stub |
|---|---|---|
| **identity-service** (`clients/identity.py`) | future identity consumers (13b). **No longer 8.1**: the Slice 8 upload resolves the store via the `identity_mirror` in-query chokepoint (`repos/stores.py`) per the slice contract — note the D37-wording tension, surfaced at the Slice 8 plan review | Service directory exists with **no src**. Stub: a client interface returning seeded mirror UUIDs in dev; circuit-open path returns `IdentityServiceUnavailableError`. |
| **Customer Master profile call** (`clients/customer_master.py`) | 1.1 `email`/`name`; 3.1 `created_by` display names | OPEN (D56, Blocker 5). Stub: deterministic placeholders from `sub` / UUID string for `created_by`, marked in code. |
| **Customer Master JWKS** | §2.1 auth | OPEN (D25/D56). Dev stub: HMAC HS256 verifier (§2.1), single-seam swap. |
| **dis-storage onboarding-staging path builder** | 2.1 sample storage | Does not exist (`paths.py` has only the canonical bronze path; hard rule 9 forbids improvising paths). Required lib extension: `build_onboarding_sample_path(tenant_uuid, sample_id) -> str` + parse counterpart, added to `libs/dis-storage` in the slice that builds 2.1. |
| **dis-core error classes** | §2.3 table | To be added to `libs/dis-core/errors.py` in this service's first implementation slice (same-commit-as-use). |
| **dis-audit `Stage` vocabulary** | 8.1 (a dedicated upload stage); config-change audit on §3/§5 writes | Current `Stage` enum covers pipeline stages only. Slice 8 ships the documented interim: `Stage.RECEIVED` + `event_data.phase="csv_upload_phase1"`, disambiguated from the worker's RECEIVED by `service_name`. Extension still owed; coordinate with D42/D45 audit follow-ups. |
| **dis-rls platform-scope read path** | 4.1/4.2/5.1/7.x ops cross-tenant reads over RLS-forced tables | `rls_session(engine, tenant_id)` is per-tenant today. Needs a platform variant inside `libs/dis-rls` (never raw SQLAlchemy). |
| **Cloud SQL read replica config** | dashboard/fleet/shadow reads (README `repos/canonical_replica.py`) | Local dev: same instance, port 5433. |
| **Pub/Sub publisher setup** | `mapping.changed` (2.5/2.8/2.9), `ingress.resubmit` (4.3), `csv.received` (8.1 — **LIVE**, Slice 8: `publisher.py`, emulator-guarded) | Emulator on `localhost:8085`; topics created by `make run-local`. Envelopes are the frozen `contracts/pubsub/*.schema.json`. |
| **Notifications store + emitters** | §6 | **Blocker 1** — undecided. |
| **Source registry** | §1.3–1.7, source display names in 4.x/§1.2 | **Blocker 2** — undecided. |
| **Resubmit lineage store** | 4.2/4.3 chain data | **Blocker 4** — undecided. |
| **Shadow execution bookkeeping** | 2.6 | **Blocker 8** — sequencing. |

---

## 7. Slice 14b surface — mapping templates + stores (template grain, D68)

**Status:** built (Slice 14b). This section is ADDITIVE and documents the first live data
endpoints. It postdates the §3–§5 surface, which was derived from the pre-template-grain
frontend demand list; where the two disagree, **this section governs for these routes and
sets the conventions for future surfaces** (the frontend adapts on its side; per the slice
contract these shapes are designed clean, not reverse-engineered).

### 7.1 Conventions set here (supersede the inherited ones for this and future surfaces)

1. **`mapping_rules` travels in the RAW D49 shape** (`{version, rename, normalize, cast,
   derive}`, the `dis_mapping.SourceMapping` document) on reads AND writes — not the §4
   flat down-rendered `dict[str,str]` view. A down-rendered read cannot round-trip an edit
   without re-inventing locale/format args, which D49 forbids (never-default locale). The
   wire type IS the validator type (one model, no drift). Display conveniences
   (`field_count`, `transform_count`) ride as derived sibling fields.
2. **Detail lookups are throw-style 404** (`ResourceNotFoundError` envelope), not §2.4's
   200-with-`null`. Under RLS, absent and other-tenant are deliberately the same 404 (no
   existence oracle). The §2.4 rule remains only for the legacy routes it lists.
3. **DRAFT is surfaced on the template surface.** §2.6's "DRAFT never surfaced in version
   lists" applied to the source-grain §3.1 version list; a template's DRAFT is its editable
   head and appears in template list/detail with wire status `"draft"`.

### 7.2 Endpoints

| Method + path | Auth | Success | Notes |
|---|---|---|---|
| GET `/v1/stores-onboarded` | tenant | 200 `OnboardedStore[]` | `identity_mirror.stores`, **in-query** tenant scoping (RLS-OFF, D41 — registered weak link; predicate lives only in `repos/stores.py`). Order: name, store_id. Store vocab lowercased (`opening\|active\|inactive\|closed`, `inclusive\|exclusive`). |
| GET `/v1/template-mapping-fields` | any authenticated | 200 `TemplateMappingField[]` | Tenant-independent; no `rls_session`, no DB — built at startup from `dis_validation.mapping_produced_columns` over the two event models + authored labels (both-directions drift check fails boot). One entry per (section, column); `mandatory` = "must be PROVIDED by rename or constant/copy/date_from_datetime derive". |
| GET `/v1/mapping-templates?source_id=` | tenant | 200 `MappingTemplate[]` | Lineage summaries via `rls_session`. Order: source_id, template_name. |
| GET `/v1/mapping-templates/{template_id}` | tenant | 200 `MappingTemplateDetail` | Full version lineage (version desc, DRAFT + DEPRECATED included), rules raw. 404 throw-style. |
| POST `/v1/mapping-templates` | tenant | **201** `MappingTemplateDetail` | Mints UUIDv7 `template_id`; writes the v1 **ACTIVE** (create-as-ACTIVE, D88: `status='ACTIVE'`, `activated_at` stamped, seq trigger-assigned), so the response carries `active_version=1` and the template is live in one step. Request body requires `template_type` (the packet axis, Slice 14d): validated against the in-code vocabulary `dis_validation.TEMPLATE_TYPES` (`sales`/`inventory_change`/`snapshot`), a clean 400 on an unknown value, then the rules are validated against that type's legal targets; it is lineage-fixed (set at creation, immutable thereafter) and surfaced on every read. `source_id` validated well-formed (`^[a-z0-9_]{1,128}$`) only; no source registry exists (Blocker 2; deliberate slice limit). |
| PATCH `/v1/mapping-templates/{template_id}` | tenant | 200 `MappingTemplateDetail` | See 7.3. |
| POST `/v1/mapping-suggestions` | tenant | 200 `MappingSuggestionResponse` | Per-column canonical-field suggestions from the client-sent profile (`columns: ColumnProfile[]`); `source` flags `llm` vs `fallback`; `suggested_target` is always a catalog key or null (validated against the catalog, never invented). **Type-aware (D90):** optional `template_type` in the body. Present + valid (`sales`/`inventory_change`/`snapshot`) scores against THAT type's per-type catalog (snapshot included); ABSENT falls back to the legacy `sales + inventory_change` union (the not-yet-retired `/upload` flow is unchanged); present + invalid is a clean 400 `invalid_template_type`. No DB, no writes; scope from the token. Tightens to REQUIRED when `/upload` retires. |

Wire models: `schemas/` (`OnboardedStore`, `TemplateMappingField`, `MappingTemplate`,
`MappingTemplateVersion`, `MappingTemplateDetail`, `MappingTemplateCreate`,
`MappingTemplatePatch`). `version` = `version_seq_per_source` (per-template);
`mapping_version_id` (global BIGSERIAL, the D22 pin) appears in payloads, never in URLs.
Role posture: Phase-1 gating only (tenant-vs-ops, §2.1); `dis:mapping_admin` is NOT yet
enforced (D25/D56 pending).

### 7.3 Write semantics (the recorded boundary calls)

- **Create writes the v1 ACTIVE** (create-as-ACTIVE, D88); **edit writes DRAFT** (the D17
  lifecycle for changes). Create needs no supersede (a fresh `template_id` cannot collide with
  the `(tenant, source, template) WHERE status='ACTIVE'` partial unique `uq_csm_active_per_source`,
  so at most one ACTIVE per template is preserved by construction; the 14a consumer `.first()`
  ordering hazard stays untriggered). It publishes **no `mapping.changed`**: the current Slice-10
  streaming consumer selects the active mapping per-chunk with a fresh keyed SELECT and **no cache**
  (`mapping.changed`/D6 side-input refresh is DEFERRED), so the next batch sees the committed ACTIVE
  immediately. NOTE: any future activate-a-new-version-in-an-existing-lineage path MUST deprecate
  the prior ACTIVE in the same transaction (supersede) and, once the consumer adopts a cached
  side-input, publish `mapping.changed`; the §7.x `/activate` spec covers that future path.
- `mapping_rules` pass a four-step gate BEFORE any write (400 `MappingConfigError`): D49
  shape/args (`SourceMapping`), non-empty rename, targets fit exactly ONE event model,
  mandatory coverage (required ∩ mapping-produced of the routed model — derived live, the
  same source as the field catalog). The row-level `value_before OR value_after` CHECK is
  deliberately NOT lifted to config validation (strictly NOT-NULL-derived; an authored
  change-template lint is a surfaced later refinement).
- **`template_type` is the packet axis (Slice 14d), lineage-fixed:** captured on create
  (required body field), validated against `dis_validation.TEMPLATE_TYPES`
  (`sales`/`inventory_change`/`snapshot`), where an unknown value is a clean 400 (not a 422), and
  the `mapping_rules` four-step gate validates targets against THIS type's legal set. It is
  immutable for the lineage (a PATCH re-validates the edited rules against the STORED type,
  never changes it) and is surfaced on every `MappingTemplate`/`MappingTemplateDetail` read.
- **PATCH lifecycle (D17):** a DRAFT edits in place; with no DRAFT, a STAGED/ACTIVE head
  yields a NEW version — status DRAFT, `predecessor_version_id` = head's
  `mapping_version_id`, seq trigger-assigned; an all-DEPRECATED lineage is 409
  (`MappingStateConflictError`).
- **`template_name` is lineage metadata, not version content:** a rename updates ALL of the
  template's rows (D17 immutability covers `mapping_rules`/`source_id`/seq/predecessor, not
  the label) and does not mint a version. Cross-template uniqueness is the DB's EXCLUDE
  constraint → 409 `MappingTemplateNameConflictError`, never a 500.
- **At most one DRAFT per template is a write-path convention** (not a DB invariant): create
  writes the ACTIVE head (no DRAFT); edit chains exactly one DRAFT off the head, then reuses it
  in place on subsequent edits. Concurrent PATCHes serialize on a
  **lock-then-reread** (two `FOR UPDATE` statements; the second, fresh-snapshot read is the
  one decided on — a single locked read resumes on a stale statement snapshot and was shown
  live to mint a double-DRAFT, since the seq trigger's `MAX` runs on a fresh snapshot and
  the unique backstop never fires). The normal interleaving therefore converges to
  edit-in-place; a lost seq race remains a 409 `MappingStateConflictError` backstop only.
- **An unprovisioned (well-formed but never-mirrored) token tenant**: reads serve `[]`/404
  exactly like a provisioned tenant with no data (no existence oracle); create, where the
  tenant FK (`fk_csm_tenant`) makes the difference detectable, is a clean 403
  `TenantScopeError` ("not provisioned in DIS"), never a 500. Any OTHER IntegrityError
  re-raises and surfaces as a 500 (rule 6: no blanket constraint-to-4xx mapping).
- `created_by_user_id` persists only when the token `sub` parses as a UUID, else NULL
  (column nullable by design; claim vocabulary unsigned — D56/Blocker 5).

### 7.4 Error additions (extend the §2.3 table)

| Error | Status | Code |
|---|---|---|
| `MappingConfigError` | 400 | `mapping_config` |
| `ResourceNotFoundError` | 404 | `resource_not_found` |
| `MappingTemplateNameConflictError` | 409 | `mapping_template_name_conflict` |
| `MappingStateConflictError` | 409 | `mapping_state_conflict` |
| `FieldCatalogDriftError` | (startup abort) | n/a — fails boot, never serves |

---

## Appendix A — Deferred demand-list endpoints (no frontend call site)

In the demand list but called nowhere in dis-ui today. Not part of the implementable surface; listed so nobody re-derives them as missing. Build only when a UI slice wires them.

| Demand # | Method + path | Why deferred |
|---|---|---|
| 3.3 | POST /v1/sources/{source_id}/mappings | "New version" button is disabled (Phase 2); no call site |
| 3.4 | POST /v1/sources/{source_id}/mappings/{version}/deprecate | ops-only; no UI |
| 4.4 | POST /v1/quarantine/{trace_id}/resolve | ops "mark resolved"; no UI (DB vocabulary `RESOLVED`/`DISMISSED` already supports it) |
| 5.2 | GET /v1/audit (search/filter) | AuditLookup is direct trace_id only in this phase |
| 7.3 | POST /v1/ops/tenants/{tenant_id}/notify | no UI; also gated on Blocker 1 |
| 7.5 | GET /v1/ops/duckdb/history | OpsQuery has sample-query buttons, no history fetch |

POS connectors (`pos-connectors.ts`, `PosConnect.tsx`) define **no endpoints**: the connect step is a disabled coming-soon shell; credential shapes are UI-proposed placeholders. No contract entry until the POS receiver phase.

## Appendix B — Frontend wiring notes for slice 13 (informational; no dis-ui change made here)

Observations an implementer of dis-ui's real mode will need; recorded here because this contract is where both sides meet. These are not backend requirements.

1. `client.ts` currently supports only GET-shaped calls (no method/body params) and always calls `response.json()` — slice 13 extends it for POST/PATCH bodies, multipart (2.1), and 204 responses (6.3/6.4).
2. Real-mode paths for every module are the §3 table; query keys already align 1:1 with endpoints.
3. The fixtures' `t_*` tenant ids and the dev personas' claims will not match mirror UUIDs; the dev-login stub should mint the seeded tenant UUID once real mode points at a live backend (§2.2).
