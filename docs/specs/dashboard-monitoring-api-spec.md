# Dashboard + Monitoring API spec (for dis-ui-server)

Status: proposal for Sanjeev. Read-only synthesis of the current frontend, verified against
real backend handlers and the existing fixture shapes at commit `8e34958`. No app code changed
by this document.

## Purpose

The tenant Dashboard (`/`) and the Monitoring surfaces (Quarantine, Audit, Notifications, and
the header bell) are mostly placeholder or fixture-backed today. The Dashboard renders one real
panel (Pipelines, from `GET /mapping-templates`) and four honest "Metrics pending" tiles; the
Monitoring screens throw in real mode (`SERVER_MODE === 'real'`) and only work against inlined
fixtures. This spec lists the endpoints that turn those surfaces real.

## Principle: the UI is already coded to fixture shapes

Every Monitoring screen and the Dashboard scaffold are already written against concrete TS types
in `services/dis-ui/src/lib/dis-ui-server/*.ts`. Those fixture shapes are the contract the UI
renders. Endpoints below should return those shapes (snake_case wire fields as already typed), so
the real-mode switch is a swap, not a screen rewrite. Where a fixture field is flagged PROVISIONAL
in its source file, that is a real open question, called out per endpoint. House conventions are
the 14b pattern: tenant from the verified token only (never body/query/header), bare resource or
array responses (no success envelope), the section 2.3 error envelope, `rls_session` Core-style
execution, 404-throw-style lookups, `/api/v1` prefix, clean snake_case error codes.

## Already real, do NOT touch

These endpoints exist and the UI consumes them in real mode. They are out of scope for this work.

- `GET /api/v1/mapping-templates[?source_id=]` and `GET /api/v1/mapping-templates/{template_id}`
  (`handlers/mapping_templates.py`). Backs the Dashboard Pipelines table and the template
  detail / Upload Data pages. Shapes: `MappingTemplate` / `MappingTemplateDetail` (see
  `mapping-templates.ts`).
- `POST /api/v1/mapping-templates` (create-as-ACTIVE, Slice 16c) and
  `PATCH /api/v1/mapping-templates/{template_id}` (edit).
- `GET /api/v1/template-types` and `GET /api/v1/template-mapping-fields?template_type=` (Slice 14d / D90).
- `POST /api/v1/mapping-suggestions` (type-aware AI mapping, D90).
- `GET /api/v1/stores-onboarded` (onboarded stores for the store picker).
- `POST /api/v1/csv-uploads` (the upload POST itself, Slice 8). Note: there is no GET form yet
  (see endpoint 1).

---

## Prioritized endpoints (by UI-unlock leverage)

Ordered so the endpoints that light up BOTH the Dashboard and a Monitoring screen come first.

### 1. List uploads / upload history

- **Method / path:** `GET /api/v1/csv-uploads` (proposed; the POST already lives at this path).
- **Purpose / screens:** the single highest-leverage endpoint. Unlocks the Dashboard "Rows
  ingested (24h)" tile, the per-source "Last received" column and the Flow panel (both
  explicitly pending in `Dashboard.tsx`), and gives Monitoring a received-events timeline. It is
  also the natural backing for ingestion-volume aggregates (endpoint 3).
- **Request:** `?source_id=` (optional), `?store_code=` (optional), `?since=` / `?until=` ISO-8601
  (optional trailing window), `?limit=` + cursor (pagination seam). All server-side.
- **Response shape:** a bare array of upload records. Ground each record in the existing
  `CsvUploadResult` (`csv-uploads.ts`), which is exactly the POST 201 body, so a history row is a
  stored upload:
  ```
  { trace_id, upload_id (^us_[a-z0-9]{12}$), tenant_id, store_id, store_code,
    source_id, template_id, gcs_uri, row_count, received_ts, status: 'received' }
  ```
  For a list, drop `gcs_uri` (internal) and add the lifecycle outcome if known from the audit
  spine (`received` vs `quarantined` vs `committed`); keep `received_ts`, `row_count`, `source_id`,
  `store_code`, `trace_id`.
- **Tenant scoping:** tenant from token; reads ride RLS via `rls_session`. Source of truth is the
  RECEIVED audit rows (Slice 30b emits one per upload) joined to the bronze chunk, or a dedicated
  uploads view; data source is Sanjeev's to confirm.
- **Effort:** PURE-AGGREGATION (query existing audit / bronze rows; no new concept).
- **15a alignment:** none directly; complements the quarantine read by sharing the `trace_id` spine.

### 2. Quarantine list + detail (+ open count), then resubmit

- **Method / path:** `GET /api/v1/quarantine` (list + open count) and
  `GET /api/v1/quarantine/{id}` (detail). Resubmit: `POST /api/v1/quarantine/{trace_id}/resubmit`.
- **Purpose / screens:** the Monitoring Quarantine console (list, filters, detail panel, resubmit
  action) and the Dashboard "Quarantine rate (24h)" tile + per-source `quarantined_open` count.
- **Request (list):** four combinable server-side filters, exactly as 15a fixes them: `source`,
  `error_type` (stage/error category), `status` (open maps to `NEW`; resolved is forward-compatible
  and empty today, D82), `time` (trailing 24h / 7d / 30d). Plus the tenant open count (independent
  of filters).
- **Response shape (list rows):** ground in `QuarantineRow` (`quarantine.ts`):
  ```
  { trace_id, source_id, source, store, error_reason, failure_stage, mapping_version,
    failed_at, status }
  ```
  Detail in `QuarantineDetail`: adds `error_context`, `original_payload`, `chain_depth`,
  `resubmits[]`. Resubmit request is PINNED (`ResubmitRequest`): `{ resubmit_type, parent_trace_id }`;
  response `ResubmitResponse`: `{ trace_id, parent_trace_id, resubmit_type, chain_depth, status:'accepted' }`.
  Chain-depth cap is 3 (architecture 6.5).
- **Tenant scoping:** tenant from token. Per 15a Task 0, the `quarantine.*` RLS posture (ON
  single-GUC vs OFF with in-query scoping) is the load-bearing derivation.
- **Effort:** read list/detail = PURE-AGGREGATION (compose `quarantine.*` + FAILURE audit rows by
  `trace_id` / `data_ingress_event_id`, D78/D79). Resubmit = NEEDS-DATA-MODEL/TOOLING (depends on
  Slice 12 replay; not just a query).
- **15a alignment:** the list + detail ARE slice-15a (`slice-15a-quarantine-read-endpoints.md`),
  already designed in detail there. Do not redesign; build to 15a. Two reconciliations the UI
  fixture forces: (a) the frontend `failure_stage` enum is `source-shape | canonical-shape | fk |
  normalization`, which 15a reconciles to the D79 `FailureCode`-derived taxonomy (one canonical
  source, no drift), so the wire value may differ and the UI mapping is a flagged single edit;
  (b) `Source` display name derivability (no source registry exists, 14b) is a 15a-surfaced gap.
  Resubmit is EXPLICITLY out of 15a scope (write/replay, Slice 12) and is a separate later endpoint;
  the cross-tenant ops variants below are also out of 15a (D76).

### 3. Ingestion volume metrics (Dashboard rollup)

- **Method / path:** `GET /api/v1/metrics/ingestion` (proposed), or fold into a `GET /api/v1/dashboard`.
- **Purpose / screens:** Dashboard "Rows ingested (24h)" tile and the 7-day Flow trend; per-source
  `rows_24h`. Also feeds the Quality panel later.
- **Request:** `?window=24h|7d|30d`, optional `?source_id=`.
- **Response shape:** ground in the `dashboard.ts` scaffold (`DashboardSummary` / `DashboardSource`):
  ```
  DashboardSummary { tenant_id, sources: DashboardSource[], latency_1h: LatencySnapshot }
  DashboardSource { source_id, name, source_type, health, rows_24h, last_ok_at, quarantined_open }
  LatencySnapshot { p50_ms, p95_ms, p99_ms }
  ```
  Note: `Dashboard.tsx` does NOT currently call `useDashboardSummary` (it renders PendingTiles to
  avoid fabricated numbers); this scaffold is the intended shape once the rollup exists. `health`
  is PROVISIONAL (`healthy | warning | failing`); derive it from real volume + quarantine signals,
  do not invent. `rows_24h` and the 7-day trend are a rollup over the upload-history / audit data
  (endpoint 1), so 1 should land first.
- **Tenant scoping:** tenant from token, own-tenant only; returns null/empty for an unknown tenant
  (UI renders the empty state).
- **Effort:** PURE-AGGREGATION (counts/percentiles over existing rows). `latency_1h` percentiles
  need pipeline timing in the audit rows; confirm availability or drop that tile honestly.

### 4. Audit trace lookup + search

- **Method / path:** `GET /api/v1/audit/traces/{trace_id}` (lookup). Search: `GET /api/v1/audit/traces?...`.
- **Purpose / screens:** the Monitoring Audit and Trace Lookup screen.
- **Request:** lookup by `trace_id`. Search (flagged seam in the fixtures): by `source_id`, `stage`,
  time window.
- **Response shape:** ground in `AuditTrace` (`audit.ts`):
  ```
  AuditTrace { trace_id, tenant_id, source_id, stages: AuditStage[], prior_trace_id }
  AuditStage { stage, at, status, mapping_version_id?, error_code? }
  ```
  `stages` vocabulary/order is PROVISIONAL (fixture shows received / validated / mapped / committed,
  and a terminal `quarantined` with `status:'error'` + `error_code`); pin it to the real audit
  Stage / FailureCode contract (D78/D79). Returns null for unknown/other-tenant (UI empty state).
- **Tenant scoping:** tenant from token, own-tenant only (the fixture filters by `snapshot.tenantId`).
- **Effort:** PURE-AGGREGATION (read audit rows by `trace_id`, group into stages). Reuses the same
  audit spine as 1 and 2.
- **15a alignment:** shares the D78/D79 failure contract with quarantine; the `error_code` and
  stage taxonomy must be the same reconciled source.

### 5. Notifications list / unread count / mark-read

- **Method / path:** `GET /api/v1/notifications?filter=unread|all|errors`,
  `GET /api/v1/notifications/unread-count`, `POST /api/v1/notifications/{id}/read`,
  `POST /api/v1/notifications/read-all`.
- **Purpose / screens:** the Monitoring Notifications screen and the header bell unread badge.
- **Request:** `filter` query (`unread | all | errors`); mark-one by `id`; mark-all no body.
- **Response shape:** ground in `notifications.ts`:
  ```
  Notification { id, severity (info|warning|error), text, source, at, read, link }
  UnreadCount { unread }
  ```
  `severity` enum and the `link` target are PROVISIONAL (the bell does not navigate on `link` yet).
- **Tenant scoping:** tenant from token; per-tenant list; mark-read mutates only the caller's rows.
- **Effort:** NEEDS-DATA-MODEL (there is no notifications table or producing path today; notifications
  would be generated from quarantine/mapping/source events). Lower priority: it is a derived feed,
  and the read-state store is new. Build after 1 to 4 give it events to summarize.

### 6. Cross-tenant ops variants (Quarantine + Audit, fleet scope)

- **Method / path:** fleet-scoped reads of endpoints 2 and 4 for ops/PLATFORM tokens.
- **Purpose / screens:** the ops MODES of Quarantine and Audit (cross-tenant). Lower priority than
  the tenant surfaces.
- **Response shape:** ground in `ops-cross-tenant.ts`: `FleetQuarantineRow = QuarantineRow &
  { tenant_id, tenant_name }`; `OpsResubmitRequest = ResubmitRequest & { tenant_id }` (PROVISIONAL,
  4.3 pins no tenant_id); `CrossTenantAuditTrace = AuditTrace & { tenant_name }`.
- **Tenant scoping:** SERVER-ENFORCED cross-tenant authorization. Requires the D76 platform see-all
  mechanism (a `dis-rls` `user_type` / PLATFORM variant plus a policy migration on every tenant
  table). Must refuse fleet scope for a non-ops token.
- **Effort:** NEEDS-DATA-MODEL (D76 RLS variant + policy migration; explicitly out of 15a scope).
- **15a alignment:** 15a is tenant-facing only and does NOT trigger the first ops-read slice (D76).

### 7. Freshness / expected-cadence

- **Method / path:** part of the Dashboard metrics (endpoint 3) once the data model exists.
- **Purpose / screens:** the Dashboard "Freshness" tile (currently a PendingTile) and the per-source
  freshness in the Flow panel.
- **Effort:** NEEDS-DATA-MODEL. See Data-model gaps: there is no expected-cadence concept anywhere,
  so freshness cannot be computed honestly today. This is a data-model change, not an endpoint.

### 8. Small win: `suggested_template_type` hint on the create 400

- **Where:** the `POST /mapping-templates` semantic-gate failure (`MappingConfigError` -> 400,
  `handlers/mapping_templates.py` via `validate_mapping_rules_for_type`). The error message already
  names the missing mandatory canonical columns.
- **Purpose / screens:** makes the CSV create error smart. The UI (Fix 2, `PreviewStep` inline
  alert) can then say "this looks like a Sales file, not Snapshot" instead of only listing missing
  columns.
- **Shape:** add `suggested_template_type` (and optionally a confidence) to the error envelope
  `details` bag. The UI already reads `err.details`. Compute it by scoring the provided columns
  against each template type's mandatory set (the same `mapping_produced_columns` the gate uses).
- **Effort:** SMALL, additive (a few lines of logic in the gate; no data-model change, no new
  endpoint). Optional but high polish-per-effort.

---

## Data-model gaps (more than a query)

- **Expected-cadence for freshness (confirmed absent).** No `expected_cadence` / cadence /
  freshness / SLA / expected-arrival concept exists in `schemas/postgres/*`, `libs/dis-canonical`,
  or `config.*`. There is no per-source schedule to compare `last_received` against. Freshness
  (endpoint 7) needs a new per-source expected-cadence field (a column on a future source-config /
  source-registry, or a small new table) plus a comparison job. Until then, "Freshness" stays an
  honest placeholder.
- **No source registry (14b, 15a-surfaced).** The human Source display name ("Manual CSV Upload",
  "Shopify POS") is not cleanly derivable; `source_id` is a kind-style composite key. This affects
  the Dashboard source `name`, the Quarantine `source` column, and Notifications `source`. A source
  registry (or a confirmed derivation) is a shared dependency for endpoints 2, 3, 5.
- **Notifications have no producing path or store.** Endpoint 5 needs both a generation rule (from
  quarantine/mapping/source events) and a read-state store.
- **Quarantine lifecycle (D82).** Only `NEW` exists; `resolved` has no producing path, so the Status
  filter's resolved value is honestly empty until a lifecycle slice lands.
- **Resubmit/replay (Slice 12).** The resubmit action (endpoint 2) and `chain_depth` lineage depend
  on replay tooling that does not exist yet; `chain_depth` is 0 today.
- **D76 platform see-all.** The cross-tenant ops reads (endpoint 6) need the RLS `user_type` /
  PLATFORM variant and a policy migration on every tenant table.
- **`ingestion_mode` (minor).** UI-only provisional field (`mapping-templates.ts`), not on the real
  contract; the real model would derive it from the source. Not blocking.

## Suggested build order

1, then 2 (to 15a) and 3 together (3 rolls up 1), then 4, then 8 (cheap), then 5, then 7
(after the cadence data model), then 6 (after D76). Endpoints 1 to 4 share one audit/quarantine
spine, so landing 1 first de-risks the rest.
