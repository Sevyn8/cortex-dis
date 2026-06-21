# DIS Architecture


**Scope:** system context, constraints, modules, data flow, isolation, audit, source onboarding, open questions. The WHY of the system.
**Out of scope:** indexed design decisions (see `decisions.md`); repository layout, per-service and per-lib structure, schemas, contracts, infra, build-vs-managed, optional components, latency budget reference (see `engineering-reference.md`); build phases, target portability, slice workflow (see `build-guide.md`); cost projection (see `cost_estimate.md`).

**Companion docs.**
- `architecture.html` — visual diagram and rendered version of this document.
- `decisions.md` — indexed register of architecture decisions (D1-Dn).
- `engineering-reference.md` — repo and component reference (the WHAT).
- `build-guide.md` — phases, conventions, operator workflow (the HOW).
- `cost_estimate.md` — infrastructure cost projection at v1.0 beta scale.

## 1. Purpose

This document describes the architecture for Ithina's data platform: the end-to-end path from ingress at the system boundary (POS/ERP/webhook/CSV) to the canonical, multi-tenant data model in Cloud SQL Postgres, with analytics-grade history in BigQuery. The goal is a managed-first, low-maintenance pipeline on GCP that meets a measured latency SLO (p50 < 3s, p95 < 8s, p99 < 15s end-to-end), enforces hard tenant data isolation, and provides full audit traceability by `trace_id` per ingested row. The DIS UI surface is served by a single backend-for-frontend service (`dis-ui-server`); authentication is delegated to Customer Master.

## 2. System Context and Constraints

### 2.1 Business shape
- **Multi-tenant.** Each tenant has one or more retail stores. Each store has one or more data sources (POS, ERP, manual upload, partner APIs).
- **Source diversity.** Mostly a known catalog of source types (common POS/ERP systems), with occasional one-offs. Field names, formats, units differ per source.
- **Cadence.** API ingress is event-driven (per-store, fires on sale/inventory events). CSV ingress is batched (ERP dumps every 15-30 minutes per store).
- **Volume target (growth stage).** Thousands of rows/second aggregate.

### 2.2 Canonical data model
Three classes of canonical table:

- **Hot table.** `canonical.store_sku_current_position`: one row per (tenant, store, sku, variant, lot), upserted on every event, RLS-enforced, denormalized with catalogue and store context. Carries `mapping_version_id` (B1, `decisions.md` D22) and `attribute_staleness_map`. Holds derived attributes (`velocity_7day`, `stock_age_days`, `unit_cost_trend_30day`) refreshed daily by the compute job.

- **Event tables (35-day rolling buffer in Cloud SQL, configurable).** Partitioned by `event_date`, daily partitions, dropped after BigQuery export AND after the configured retention window.
  - `canonical.store_sku_sale_events` — sale line-items (SALE, RETURN, VOID). Largest volume.
  - `canonical.store_sku_change_events` — all other state changes (inventory, price, cost, regulatory, status, catalogue). Polymorphic structure via `event_category` + `event_subtype` plus typed numeric shortcut columns.

- **Signal history (daily, in Cloud SQL with 35-day retention; BQ archives long-term).** `canonical.store_sku_signal_history`: one row per SKU per `as_of_date`, append-only, holds the daily-computed derived attributes for incremental compute and backtesting.

**BigQuery is the long-term archive.** Cloud SQL holds the rolling retention window (35 days default, configurable). The daily Cloud SQL → BigQuery export runs from day 1; partitions are dropped from Cloud SQL only after they age beyond retention. This gives ops a 35-day SQL replay surface in Cloud SQL plus permanent BigQuery archive. Retention is sized for v1.0 beta scale (~150K events/day across 5 tenants × ~25 stores); the configurable retention allows raising or lowering this as scale changes.

### 2.3 Write semantics

#### 2.3.1 Hot tier
- **Composite natural key:** `(tenant_id, store_id, sku_id, sku_variant, sku_lot_batch)` with `NULLS NOT DISTINCT`.
- **Upsert is column-scoped merge**, not row replace. Each source is authoritative for a subset of canonical columns; an upsert touches only those columns and leaves others as-is. `attribute_staleness_map` records when each tracked attribute was last refreshed.
- **Conflict resolution.** Event-time wins: a late-arriving older event does not overwrite newer state in the hot table, but is still appended to the event table.
- **Atomic dual-write.** Streaming consumer writes both the hot-table upsert and the event-table insert in a single Cloud SQL transaction. Both succeed or both roll back. RLS context (`SET LOCAL app.tenant_id`) covers both writes.

#### 2.3.2 Event tables: strictly append-only with corrections preserved

Event tables hold every event the system ever received for a given source event, including corrections. The same `source_event_id` can appear multiple times: once for the original, once per correction.

- **Dedup scope (the "same source event" key):** `(tenant_id, store_id, source_id, source_event_id)`. Each source-at-a-store is its own numbering namespace. See `decisions.md` D33.
- **No UNIQUE constraint on event tables.** Event tables stay strictly append-only. Every correction is recorded as a separate row.
- **Latest-wins at read time.** Queries and dbt views over event tables apply `ROW_NUMBER() OVER (PARTITION BY tenant_id, store_id, source_id, source_event_id ORDER BY event_ts DESC, received_ts DESC) = 1` to get the current truth. Reports default to the latest version; correction history is available by removing the filter.
- **Hot table follows event-time-wins.** The hot table reflects the latest correction. Hot tier is "what is true now"; event tables are "what we ever heard".
- **Streaming consumer write logic.** On each canonical row produced: INSERT into the event table unconditionally. UPSERT into the hot table with event-time-wins. The application checks for prior events with the same dedup key as part of the audit emission, not as a write gate.

#### 2.3.3 Audit on duplicates

Every event-table INSERT emits an audit record. When the dedup key already exists for a prior event:

- `outcome = DUPLICATE_NOOP` if the new event's canonical payload is identical to the prior latest event. Most retries land here.
- `outcome = DUPLICATE_OVERWRITTEN` if the new event's payload differs (a correction from the source). High-signal; ops may alert on patterns.
- `prior_trace_id` is recorded for traceability.

Ops can answer "what's the duplicate rate per tenant?" by querying audit; no DB scan needed.

#### 2.3.4 Idempotency on retry

A retry of the same chunk (same `trace_id`) runs through the full pipeline. The source-event-id dedup at the event-table audit layer catches retries as `DUPLICATE_NOOP` events. Retries do not cause data corruption; they do cause repeated mapping/validation compute. Acceptable at v1.0 beta scale; see §9.2 for the forward note on trace-level dedup at higher scale.

### 2.4 Latency SLO
End-to-end latency from event-at-store to reflected-in-`store_sku_current_position`, measured as `commit_ts - received_ts` via the Cloud SQL `audit.events` table (Phase 1; BigQuery `audit_events` from Phase 3 onward):
- **p50 < 3 seconds**
- **p95 < 8 seconds**
- **p99 < 15 seconds**

Error budget: 5% over 30 days. Breach triggers a review (not an auto-page, since most causes are external infra hiccups, not architectural regressions). The architecture targets the warm path at ~500ms-2s under healthy steady-state load.

### 2.5 Isolation
- **Data isolation (absolute).** No tenant sees another tenant's data. Enforced by Postgres RLS on canonical tables with no `BYPASSRLS`.
- **Performance isolation.** B3 (one of the blocker-grade open questions, see §9) remains open. Default: shared pipeline with per-tenant receiver rate limits and fair-share consumer scheduling; revisit triggers documented when a tenant becomes noisy enough to matter.

### 2.6 Failure handling
- **Receiver is permissive.** Auth + structural validity gate at the boundary; semantic failures (mapping errors, validation breaches) are caught downstream and quarantined, not bounced synchronously.
- **Tenant feedback loop.** Quarantine surfaces in a tenant-facing UI with alerts/notifications.
- **Replay.** Both tenant-side resubmit (fresh ingress) and Ithina-side replay from bronze/GCS are supported.

### 2.7 Audit
Every event carries a `trace_id` from receiver onward. Every pipeline stage emits a per-row audit event keyed by `trace_id`. Debugging a row is a `SELECT ... WHERE trace_id = '...'`.

Phase 1 lands audit events in Cloud SQL `audit.events` so the dis-ui-server audit-lookup feature can ship without waiting on the cloud-project setup BigQuery requires (see `decisions.md` D34). Phase 3 adds the Cloud SQL → BigQuery archive job for `audit_events`; BigQuery remains the long-term audit home as in `decisions.md` D7 and D29.

### 2.8 Identity store isolation
Tenant and store metadata lives in **Customer Master**, the Auth0-integrated identity, auth, and RBAC system that is the single source of truth for user identity and authorization across Sevyn8 products (not just DIS). Customer Master is physically separate from the data-platform DB; the data platform never queries it directly. Access is mediated by a **Tenant/Store Identity Service** (DIS-internal, see `decisions.md` D2) that wraps Customer Master with a cache and circuit breaker. Cross-DB FK is impossible at the engine level; equivalent guarantees are reconstructed via a replicated mirror table inside the data-platform DB with real Postgres FKs pointing to it.

User authentication and RBAC for the DIS UI is delegated entirely to Customer Master. DIS does not maintain user records; the DIS UI accepts Customer Master tokens, and dis-ui-server (see `decisions.md` D17) validates them. Customer Master is treated as an external dependency, not a DIS sub-component.

### 2.9 PII handling
Personal identifiers (phone, email, loyalty_id, PAN, Aadhaar, and other fields per tenant policy) are tokenized at the receiver via deterministic HMAC with per-tenant keys before any persistence. Bronze and canonical never carry raw PII. Right-to-erasure (DPDPA, GDPR) becomes a token-vault delete: deleting the key invalidates all tokens for that tenant. Tokenization is deterministic so joins on tokenized fields still work; per-tenant key prevents cross-tenant join-inference attacks. See `decisions.md` D18 for the PII module.

## 3. High-Level Architecture

```
   ════════════════════════════════════════════════════════════════════════════
                            ITHINA DATA PLATFORM
   ════════════════════════════════════════════════════════════════════════════

   ┌──────────────────────── INGRESS (Ithina-owned) ───────────────────────────┐
   │                                                                           │
   │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
   │   │ POS/ERP/etc. │  │ Manual CSV   │  │ ERP CSV POST │  │ External APIs│  │
   │   │ push to API  │  │ upload UI    │  │ per-tenant   │  │ (Ithina pulls│  │
   │   │ / webhook    │  │              │  │ endpoint     │  │  response =  │  │
   │   │              │  │              │  │              │  │  ingress)    │  │
   │   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
   │          │                 │                 │                 │          │
   │          ▼                 ▼                 ▼                 ▼          │
   │   ┌─────────────────────────────────────────────────────────────────┐     │
   │   │  RECEIVER (containerized services, per channel)                 │     │
   │   │  - authenticates caller (Customer Master tokens or machine cred)│──┐  │
   │   │  - resolves tenant_id, store_id via Identity Service ───────────┼─┐│  │
   │   │  - generates trace_id                                           │ ││  │
   │   │  - TOKENIZES PII (HMAC, per-tenant key)                          │ ││  │
   │   │                                                                 │ ││  │
   │   │   ┌──────────────────────────────────────────────────────┐      │ ││  │
   │   │   │ [CSV paths] PRE-FLIGHT CHECK                         │      │ ││  │
   │   │   │   Option A (baseline): basic file checks             │      │ ││  │
   │   │   │     size, MIME, header row present                   │      │ ││  │
   │   │   │   Option B (recommended): DuckDB pre-flight  ◄── NEW │      │ ││  │
   │   │   │     SELECT count(*), columns, null counts, type      │      │ ││  │
   │   │   │     sniff FROM read_csv('gcs://...')                 │      │ ││  │
   │   │   └──────────────────────────────────────────────────────┘      │ ││  │
   │   │                                                                 │ ││  │
   │   │   - writes raw payload -> GCS                                   │ ││  │
   │   │   - writes enriched chunk row -> Bronze Postgres                │ ││  │
   │   │   - publishes -> Pub/Sub: ingress.ready                         │ ││  │
   │   │   - returns 2xx (permissive accept)                             │ ││  │
   │   └────────────────────────────┬────────────────────────────────────┘ ││  │
   │                                │                                      ││  │
   └────────────────────────────────┼──────────────────────────────────────┼┼─ ┘
                                    │                                      ││
                                    │             ┌────────────────────────┘│
                                    │             │                         │
                                    │             ▼                         │
                                    │   ┌───────────────────────┐           │
                                    │   │ TENANT/STORE IDENTITY │           │
                                    │   │ SERVICE               │           │
                                    │   │ (containerized svc +  │           │
                                    │   │  Redis cache; admin   │           │
                                    │   │  DB behind it)        │           │
                                    │   │ resolve() / validate()│◄──────────┤
                                    │   └──────────┬────────────┘           │
                                    │              │                        │
                                    │              │ identity.changed       │
                                    │              ▼ (Pub/Sub)              │
                                    │   ┌──────────────────────┐            │
                                    │   │ Mirror Sync Consumer │            │
                                    │   │ (containerized svc)  │            │
                                    │   └──────────┬───────────┘            │
                                    │              │ writes                 │
                                    │              ▼                        │
                                    │   ┌──────────────────────────────┐    │
                                    │   │ Cloud SQL Postgres           │    │
                                    │   │ identity_mirror schema:      │    │
                                    │   │  - tenants             │    │
                                    │   │  - stores (status)     │    │
                                    │   │ (canonical FK ─────────────► │    │
                                    │   │  reference these mirrors)    │    │
                                    │   └──────────────┬───────────────┘    │
                                    │                  │ real FK            │
                                    ▼                  │ enforced           │
                          ┌───────────────────┐        │                    │
                          │  Pub/Sub          │        │                    │
                          │  ingress.ready    │        │                    │
                          └─────────┬─────────┘        │                    │
                                    │                  │                    │
   ┌────────────────────────── MIDDLE (ELT) ───────────┼────────────────────┼──┐
   │                                                   │                    │  │
   │   ┌───────────────────────────────────────────────┼────────────────┐   │  │
   │   │  STREAMING CONSUMER (containerised service)                  │                │   │  │
   │   │                                               │                │   │  │
   │   │   1. Read Pub/Sub msg (ingress.ready or       │                │   │  │
   │   │      ingress.resubmit)                        │                │   │  │
   │   │   2. Fetch chunk from GCS (bronze ptr)        │                │   │  │
   │   │   3. Mapping lookup BY VERSION                │                │   │  │
   │   │      (config side input; mapping.changed PS   │                │   │  │
   │   │      triggers refresh)                        │                │   │  │
   │   │   4. Validate tenant/store FK (identity_mirror│                │   │  │
   │   │      with Identity Service fallback)          │                │   │  │
   │   │                                               │                │   │  │
   │   │   5. PRE-MAPPING VALIDATION (source-shape)    │                │   │  │
   │   │      Pandera source-shape suite               │                │   │  │
   │   │      Fail -> quarantine (chunk-level)         │                │   │  │
   │   │                                               │                │   │  │
   │   │   6. Apply mapping:                           │                │   │  │
   │   │      rename -> normalize -> cast -> derive    │                │   │  │
   │   │      Normalize handles format variance        │                │   │  │
   │   │      (dates, decimals, TZ, units, enums)      │                │   │  │
   │   │      via declarative transforms + escape      │                │   │  │
   │   │      hatch.  Fail -> quarantine.              │                │   │  │
   │   │                                               │                │   │  │
   │   │   7. POST-MAPPING VALIDATION (canonical-shape)│                │   │  │
   │   │      Pandera canonical-shape suite            │                │   │  │
   │   │      Fail -> quarantine (per-row)             │                │   │  │
   │   │                                               │                │   │  │
   │   │   8. STAMP mapping_version_id on every row    │                │   │  │
   │   │   9. Cloud SQL health probe; on unhealthy ->  │                │   │  │
   │   │      pipeline.dlq topic (instead of canonical)│                │   │  │
   │   │  10. Branch: valid -> canonical sinks         │                │   │  │
   │   │            invalid -> quarantine              │                │   │  │
   │   │  11. Emit audit event per stage               │                │   │  │
   │   │                                               │                │   │  │
   │   └───┬───────────────────┬───────────────────────┼───────────┬────┘   │  │
   │       │                   │                       │           │        │  │
   └───────┼───────────────────┼───────────────────────┼───────────┼────────┘  │
           │                   │                       │           │           │
           ▼                   ▼                       ▼           ▼           │
   ┌──────────────────────────────────────────┐  ┌─────────────┐ ┌──────────┐  │
   │ CLOUD SQL POSTGRES                       │  │ Pub/Sub     │ │ BigQuery │  │
   │                                          │  │ quarantine  │ │ audit_   │  │
   │ ┌──────────────────────────────────────┐ │  │     │       │ │ events   │  │
   │ │ canonical schema                     │ │  │     ▼       │ └──────────┘  │
   │ │   HOT tables (RLS, mapping_version_id│ │  │ ┌─────────┐ │               │
   │ │   on every row)                      │ │  │ │ Quarant.│ │               │
   │ │     - store_sku_current_position        │ │  │ │ drainer │ │               │
   │ │     - sibling hots                   │ │  │ │ (svc)   │ │               │
   │ │   HISTORY tables (35 days,           │ │  │ └────┬────┘ │               │
   │ │   mapping_version_id NOT NULL)       │ │  │      │      │               │
   │ │  FK ──► identity_mirror.stores │ │  │      ▼      │               │
   │ │  FK ──► identity_mirror.tenants│ │  │ ┌─────────┐ │               │
   │ │  Schema evolution via Alembic        │ │  │ │ CloudSQL│ │               │
   │ └──────────────────────────────────────┘ │  │ │ quarant.│ │               │
   │                                          │  │ │ table   │ │               │
   │ ┌──────────────────────────────────────┐ │  │ │ (RLS)   │ │               │
   │ │ identity_mirror schema               │ │  │ └────┬────┘ │               │
   │ └──────────────────────────────────────┘ │  │      │      │               │
   │                                          │  └──────┼──────┘               │
   │ ┌──────────────────────────────────────┐ │         │                      │
   │ │ config schema                        │ │         │                      │
   │ │   - source_mappings (versioned ◄─B1) │ │         │                      │
   │ │   - expectation_suite_refs           │ │         │                      │
   │ │     (per tenant, source, version)    │ │         │                      │
   │ └──────────────────────────────────────┘ │         │                      │
   └──────────────┬───────────────────────────┘         │                      │
                  │                                     │                      │
                  │ all UI reads/writes via dis-ui-server     │                      │
                  ▼                                     │                      │
   ┌──────────────────────────────────────────┐         │                      │
   │ dis-ui-server (BFF)                            │         │                      │
   │ Handlers: sample_upload | onboarding_    │         │                      │
   │ review | mapping_crud | quarantine |     │         │                      │
   │ audit | duckdb_query                     │         │                      │
   │ Hosts onboarding/ sub-module in-process  │         │                      │
   │ Publishes: ingress.resubmit, mapping.    │         │                      │
   │ changed                                  │         │                      │
   └──────────────┬───────────────────────────┘         │                      │
                  │                                     │                      │
                  ▼                                     │                      │
                  ┌──────────────────────────────────────────────────┐         │
                  │ DIS UI (single containerized service)            │         │
                  │ Auth via Customer Master                         │         │
                  │ Sub-modules: Auth | Sample upload |              │         │
                  │ Onboarding review | Mapping CRUD |               │         │
                  │ Quarantine console (tenant + ops slices) |       │         │
                  │ Audit / trace lookup | DuckDB query panel (ops)  │         │
                  └──────────────────────────────────────────────────┘         │
                                                                               │
                  nightly, retail off-hours                                    │
                  ▼                                                            │
   ┌──────────────────────────────────────────┐                                │
   │ Cloud Scheduler                          │                                │
   │   -> containerized job:                  │                                │
   │     - run Pandera quality gate           │                                │
   │     - load yesterday's history -> BQ     │                                │
   │     - delete > 35d from Cloud SQL       │                                │
   └──────────┬───────────────────────────────┘                                │
              │                                                                │
              ▼                                                                │
   ┌──────────────────────────┐                                                │
   │ BIGQUERY                 │                                                │
   │ canonical_history.*      │                                                │
   │ <-- analytics consumers  │                                                │
   │ models via dbt           │                                                │
   └──────────────────────────┘                                                │

   ┌──────────────────────── EGRESS / CONSUMERS ──────────────────────────────┐
   │   Cloud SQL READ REPLICA  |  BigQuery (analytics)  |  Audit console      │
   └──────────────────────────────────────────────────────────────────────────┘

   LEGEND
   B1, B2, B3 = blocker-grade open questions (see §9). B1 is resolved.
```

## 4. Modules

This section describes each major module at a high level: what it is, what it does, and the role it plays. Implementation details (libraries, schemas, code) are deliberately omitted; those belong in module-level design docs.

### 4.1 Receivers and the CSV-upload worker
**What it is.** The system's ingress surface. v1.0 ships **CSV upload via DIS UI** in two operational halves:
- **Phase 1 — `upload_session` endpoint inside `dis-ui-server`.** A synchronous handler called by the DIS UI to start an upload. Validates the user's Customer Master session, resolves identity, mints `trace_id`, builds the canonical GCS path via `libs/dis-storage`, and returns a 15-minute signed PUT URL. No bronze write, no Pub/Sub publish.
- **Phase 2 — `csv-ingest-worker` service.** A Pub/Sub-subscribed worker triggered by GCS object-finalized notifications when the tenant's PUT completes. Runs DuckDB structural preflight, PII tokenization, bronze metadata write via `libs/dis-rls`, `ingress.ready` publish, and audit emission. Reads (never mints) `trace_id` from the GCS object path.

Future channels (API/webhook, per-tenant ERP CSV POST, external-API puller) each ship as their own receiver service. All deferred for v1.0.

**Role.** Whichever the channel, the ingress surface authenticates the caller (or runs under an already-authenticated context, for the dis-ui-server endpoint), attaches identity (`tenant_id`, `store_id`) and tracing (`trace_id`), **tokenizes PII fields (`decisions.md` D24)** when applicable, persists the raw payload to GCS and a metadata-only enriched chunk record to bronze Postgres, and notifies the pipeline by publishing to Pub/Sub `ingress.ready`. The ingress surface is permissive: it accepts anything structurally valid and lets the pipeline handle semantic validation.

**Why the CSV-upload split (`decisions.md` D36).** The DIS UI is the only initiator of CSV upload Phase 1. Spinning up a separate `receiver-csv-upload` service for what amounts to "validate session + mint trace_id + sign GCS URL" would force the UI to talk to two backends and duplicate the Customer Master auth integration. Folding Phase 1 into `dis-ui-server` keeps the BFF promise (D26): one URL the UI calls. Phase 2 stays a separate service because it has a genuinely different shape: event-triggered (not request-response), CPU-heavy (DuckDB preflight on multi-MB CSVs), scales with data volume rather than UI concurrency, and retries on transient failure. Calling Phase 2 a "worker" (not a "receiver") reflects its operational nature: queue consumer, not HTTP request handler.

**Why future channels each get their own receiver service.** API/webhook, ERP CSV POST, and reverse-API pull each have distinct auth profiles (machine credentials, per-tenant API keys, endpoint-config-bound identity), distinct triggers (push vs. pull), and distinct rate profiles. Splitting per-channel keeps each receiver simple and lets channels be deployed and scaled independently. CSV-upload is the outlier because it's UI-initiated and trivially small in Phase 1.

**Auth posture.** CSV upload Phase 1 (`dis-ui-server` endpoint): Customer Master session token, verified by `dis-ui-server`'s `auth/` module. CSV upload Phase 2 (worker): no caller to authenticate; identity is inherited from the upload session via `Identity Service.resolve_from_upload`. API/webhook and reverse-API: machine credentials (bearer token, API key, mTLS). ERP CSV POST: per-tenant API key or mTLS. All identity is resolved via the Identity Service after auth (`decisions.md` D2).

### 4.2 Tenant/Store Identity Service
**What it is.** A small containerized service backed by an in-memory or Redis cache, sitting in front of **Customer Master** (the Auth0-integrated identity system; see §2.8).
**Role.** The only path by which the data platform reads tenant and store identity. Receivers call it to resolve identity from auth context; the pipeline calls it to validate that a tenant/store still exists at processing time. The service also publishes `identity.changed` events when Customer Master data is updated, so that downstream caches and mirrors stay in sync.
**Resilience.** Stale-while-error caching (`decisions.md` D28): on Customer Master error, cache continues to serve entries up to 5 minutes stale. Streaming consumer `validate()` calls fall back to direct `identity_mirror` read when the Identity Service circuit is open.

**Interface (gRPC or REST, internal-only):**
- `resolve_from_token(jwt)` → identity. Used by API/webhook receivers that authenticate callers with a bearer token.
- `resolve_from_upload(upload_id)` → identity. Used by the CSV upload paths (DIS UI session and per-tenant ERP POST endpoint), where identity is bound to an upload session, not a request token.
- `resolve_from_endpoint(cfg_id)` → identity. Used by the reverse-API puller, where identity is bound to the endpoint config registered for that pull.
- `validate(tenant_id, store_id)` → bool. Used by the Beam pipeline as a FK-substitute pre-check at processing time, catching tenant/store deletions that happened between receive and process.

All resolve methods return `{tenant_id, store_id, + metadata}` from the cache when warm; cache miss falls through to Customer Master, populates the cache, and returns. `validate` is a lightweight existence + active-flag check, also cached.

**Why it exists.** Physical separation of Customer Master is non-negotiable, and direct cross-DB calls would couple ingress latency to Customer Master latency. The service provides a single, cached, audited access point, with channel-specific resolve methods so each receiver type uses the right identity source.

### 4.3 Mirror Sync Consumer
**What it is.** A containerized service that maintains the `identity_mirror` schema. Ships with two modes: a Pub/Sub subscriber on `identity.changed` (the architectural target), and a DB-pull mode that reads tenant/store records directly from Customer Master's Postgres database.
**Role.** Maintains a small mirror of identity data (`tenants`, `stores`) inside the data-platform Postgres. Acts as the local source of truth for FK references from canonical tables. Replicates Customer Master's `status` verbatim (upsert-only; never deletes — there is no `is_active` column), so canonical rows do not get cascade-killed by a Customer Master cleanup.
**Why it exists.** Postgres FK cannot reach across instances. The mirror reconstitutes equivalent integrity inside the data-platform DB.
**Two modes (see `decisions.md` D35).** DB-pull is the v1.0 launch mode and ships first, because Customer Master does not yet emit `identity.changed`. Pub/Sub consumer mode activates once Customer Master emits the events. Both modes share the same upsert path, so canonical FK behaviour is identical. DB-pull persists past launch as a reconciliation mechanism even after Pub/Sub is live.

### 4.4 Bronze
**What it is.** A landing zone for enriched ingress chunks, split between Cloud SQL Postgres (chunk metadata + small payloads) and GCS (raw blobs for CSV).
**Role.** Records every ingress event in its as-received form, enriched with identity and tracing metadata but not yet transformed. Serves three purposes: audit, replay, and decoupling the receiver from pipeline backpressure.
**Why it exists.** Without bronze, a failed downstream transformation has no source to retry from, and the tenant has no proof of what was sent.

### 4.5 Ingress message bus
**What it is.** The `ingress.ready` Pub/Sub topic and its subscriptions.
**Role.** The handoff between receivers and the pipeline. Carries a small notification per ingress chunk (identity + pointer to bronze and GCS), not the payload itself. Provides at-least-once delivery, dead-letter handling, and natural backpressure.
**Why it exists.** Decouples receiver throughput from pipeline throughput. Lets the pipeline restart, scale, or pause without losing ingress events.

### 4.6 Streaming Consumer
**What it is.** A containerised service that consumes `ingress.ready` (and `ingress.resubmit`) and runs the full ELT middle. Runs as a Pub/Sub-pull subscriber loop with manual batching of ~500 rows per tenant transaction. Specific compute platform is a deployment choice (see `engineering-reference.md`); migration trigger for higher-throughput runtimes per `decisions.md` D4.
**Role.** Reads ingress notifications, fetches the chunk from bronze + GCS, looks up the per-(tenant, source) active mapping by version, validates tenant/store identity (against `identity_mirror` with Identity Service fallback), runs pre-mapping (source-shape) validation, applies the mapping in four sub-stages (rename, normalize, cast, derive), runs post-mapping (canonical-shape) validation, **stamps `mapping_version_id` on every produced canonical row (`decisions.md` D22)**, then **atomically writes both the hot-table upsert and the matching event-table insert in one Cloud SQL transaction (`decisions.md` D30)**. Routes valid rows to the canonical sinks and invalid rows to quarantine. Implements the circuit-breaker + DLQ pattern (`decisions.md` D27). Emits an audit event at every stage (each carrying `mapping_version_id`).
**Atomic dual-write detail.** For each canonical row produced: UPSERT into `store_sku_current_position` (with column-scoped merge and event-time-wins logic) and INSERT into the matching event table (`store_sku_sale_events` for sale events, `store_sku_change_events` for everything else). Same transaction; same `app.tenant_id` context; either-or-neither semantics. Event tables are strictly append-only with no UNIQUE constraint; corrections from the source are recorded as separate rows with the same `(tenant_id, store_id, source_id, source_event_id)` key, deduplicated at read time. See §2.3.2 for the dedup policy.
**Why it exists.** This is the only place in the system where per-tenant, per-source transformation logic lives. Centralizing it keeps mapping rules declarative and runtime managed.

### 4.7 Mapping & Validation Configuration
**What it is.** A configuration store in the data-platform Postgres (`config.source_mappings`), holding versioned mapping rules and Pandera validation suite references, per (tenant, source).
**Role.** The contract between an external data source and the canonical schema. Edited via dis-ui-server mapping CRUD. The pipeline reads it as a refreshing side input; `mapping.changed` events trigger immediate cache refresh (`decisions.md` D6).
**Why versioned.** B1 (`decisions.md` D22) requires that every canonical row carry the `mapping_version_id` that produced it. Mapping versions are immutable; edits create new versions. Active mapping is the latest with `status=active`. Old versions remain queryable indefinitely.
**Why it exists.** Source diversity is the central operational challenge of the platform. Without a config-driven mapping layer, every new source would require a code change and a deploy.

### 4.8 Canonical Storage (Cloud SQL Postgres)
**What it is.** The canonical schema in Cloud SQL Postgres, holding the hot table, 35-day rolling event buffer, and recent signal history.
- `canonical.store_sku_current_position` — hot table; one row per SKU instance.
- `canonical.store_sku_sale_events` — sale event buffer; partitioned by event_date; 35-day retention.
- `canonical.store_sku_change_events` — all other change events; partitioned by event_date; 35-day retention.
- `canonical.store_sku_signal_history` — daily computed signals; partitioned by as_of_date; 35-day retention.
**Role.** The system of record for current state; the rolling event log for the retention window; the rolling history of daily signals. Every event-bearing row carries `mapping_version_id BIGINT NOT NULL` (`decisions.md` D22). Read by the product (via read replica), written exclusively by the streaming consumer (current_position + events) and the daily compute job (signal_history + current_position updates). RLS-enforced for tenant isolation, FK-linked to the identity mirror for referential integrity. Schema evolution via Alembic (`decisions.md` D23).
**Why it exists.** Cloud SQL was a stated constraint. It also fits: relational integrity, transactional upserts (dual-write per `decisions.md` D30), row-level security, and predictable read latency are all native strengths. At beta scale (~150K events/day, 5 tenants × ~25 stores), the 35-day buffer holds ~5M event rows comfortably on a single Cloud SQL instance.

### 4.9 Quarantine
**What it is.** A Pub/Sub topic for failed rows, a Cloud SQL table that holds them with full context, and a tenant-facing UI surface.
**Role.** The asynchronous error channel. Failed rows land here with their original payload, the reason for failure (in human-readable form when GE or Pandera is in use), and links back to the ingress chunk for replay.
**Why it exists.** Receivers are permissive by design; the failure surface has to be somewhere. Quarantine gives tenants and ops a single place to see, understand, and act on bad data.

### 4.10 Audit Store
**What it is.** A structured per-event audit record store. Phase 1: Cloud SQL `audit.events`. Phase 3 onward: BigQuery `audit_events` as the long-term archive, with Cloud SQL serving as a rolling buffer (35-day retention matching event tables).
**Role.** Captures one row per (trace_id, stage, status) emitted by the pipeline. Provides the end-to-end traceability the system promises: any row's lifecycle is one SQL query away.
**Why it exists.** Logs are not queryable enough for production debugging at scale. A dedicated structured store, separate from operational logs, makes audit a first-class concern.
**Why Phase 1 in Cloud SQL (see `decisions.md` D34).** The dis-ui-server audit-lookup feature needs SQL-queryable audit. Routing audit through Cloud SQL during Phase 1 avoids waiting on the cloud-project setup BigQuery requires. The BigQuery target remains the architectural endpoint, added in Phase 3 alongside the rest of the BigQuery offload (build-guide.md Slice 16).

### 4.11 Analytics Store (BigQuery `canonical_history`)
**What it is.** The long-term home of history data, populated by a nightly batch job from Cloud SQL.
**Role.** Serves analytics consumers (BI tools, ML feature pipelines, internal dashboards) without competing with the operational Cloud SQL workload. Holds the full retention; Cloud SQL holds only the recent slice.
**Why it exists.** History grows without bound; Cloud SQL is the wrong shape for analytics at scale (cost, query patterns). BigQuery is the natural home, and the nightly cadence matches the analytics tolerance.

### 4.12 Nightly Batch Job
**What it is.** A Cloud Scheduler trigger invoking two containerized jobs during retail off-hours.
**Two jobs, sequential:**
1. **Daily compute job (`decisions.md` D20).** Reads yesterday's signal_history + today's events; computes new signals; INSERTs into `store_sku_signal_history`; UPDATEs `store_sku_current_position`.
2. **Daily Cloud SQL → BigQuery export + retention eviction.** Copies day N's partitions of `store_sku_sale_events`, `store_sku_change_events`, and `store_sku_signal_history` to `canonical_history.*` in BigQuery via WRITE_TRUNCATE per partition. After successful export, drops Postgres partitions older than the configured retention window (default 35 days).
**Role.** Keeps the daily-compute pipeline running, the BigQuery archive populated, and the Cloud SQL footprint bounded by retention. Idempotent; safe to re-run.
**Why two jobs.** The compute job depends on yesterday's signal history being present in Cloud SQL. The export job runs after compute completes; partition drop happens only for partitions older than retention. Order matters; sequence enforced by the scheduler.
**Why daily copy + delayed drop.** BigQuery archive is built from day 1 onward, so long-term analytics is always available. Cloud SQL holds 35 days for ops replay and recent investigations. Retention is configurable for different deployments.
**Failure handling.** If compute fails, export does not run; tomorrow's compute uses the slow path (full window from BQ) for any affected SKUs. If export fails, partition drop is skipped; the next day's run catches up. The export is idempotent (WRITE_TRUNCATE replaces partition content); re-running the export for an already-loaded partition is safe.

### 4.13 DIS UI (Data Integration System UI)
**What it is.** A single containerized service that hosts the data platform's entire UI surface. One deployment, one container, **delegates auth to Customer Master**. All user-facing screens for both tenants and Ithina ops live here as sub-modules.

**Sub-modules:**
- **Auth.** Accepts Customer Master-issued tokens; passes them to dis-ui-server on every backend call. The UI does not maintain its own user store; Customer Master is the source of truth.
- **Sample upload.** Front-end for source onboarding. Lets the operator (or tenant) upload a sample file or paste a sample payload. Calls dis-ui-server `sample_upload` handler (which invokes the in-process onboarding sub-module).
- **Onboarding review.** Side-by-side display of proposed mapping + sample data with low-confidence rows highlighted. Operator overrides, dry-runs against the sample, and approves to `staged`. Hosts the shadow-rollout review screen for promoting `staged` → `active`.
- **Mapping config CRUD.** Manage `config.source_mappings`: view active and staged mappings per tenant/source, edit (creates a new version, leaves prior version intact), deprecate, view version history. Mapping version is surfaced on canonical rows (`decisions.md` D22 / B1).
- **Quarantine console.** Two views:
  - *Tenant-facing slice:* current tenant's quarantined rows with human-readable failure reasons, links to Pandera suite failure documentation, and resubmit action. Resubmit publishes `ingress.resubmit`.
  - *Ops slice:* cross-tenant view, filter by source, failure type, time range; ops actions (trigger Ithina-side replay from bronze, mark resolved).
- **Audit & trace lookup.** Search Cloud SQL `audit.events` (Phase 1; BigQuery `audit_events` from Phase 3 onward) by `trace_id`, `tenant_id`, `store_id`, or time range. Renders the per-stage lifecycle of a chunk or row, including `mapping_version_id` at every post-mapping stage.
- **DuckDB ad-hoc query panel (ops only).** Operator pastes a GCS bronze blob URI and SQL; backend runs the query via DuckDB and returns results. Used for debugging without spinning up the streaming consumer or loading to BigQuery.

**Role.** The single human-facing front door to the data platform. Calls one backend: dis-ui-server (`decisions.md` D17). Data-plane services (Identity, Mirror Sync, Streaming Consumer, Quarantine Drainer) are headless; the UI never calls them directly.

**Why it exists as a single container.** Earlier drafts described UI surfaces scattered across modules ("ops console," "onboarding UI," "quarantine UI"). These share auth, session, layout, and navigation. One container avoids duplicating that infrastructure and gives users one URL.

### 4.14 DuckDB (recommended addition)
**What it is.** An embedded analytical SQL engine, used in the receivers (CSV pre-flight) and the dis-ui-server DuckDB query panel handler (GCS bronze inspection).
**Role.** Cheap, fast SQL over CSV and Parquet in GCS, in-process, without spinning up the streaming consumer or loading into BigQuery. Used to reject obviously malformed CSVs at the boundary and to give ops a one-shot debugging tool.
**Why it exists as optional.** Both jobs can be done without DuckDB (custom parsing in receivers, manual BigQuery loads in ops), but DuckDB makes them dramatically cheaper to write and operate.

### 4.15 Validation Engine: Pandera (see `decisions.md` D4a)
**What it is.** Pandera, the declarative data-validation library.
**Role.** Source-shape (pre-mapping) and canonical-shape (post-mapping) suites. Suites are versioned per (tenant, source, version), stored in `config.expectation_suite_refs`, and run as Python objects in the streaming consumer.

### 4.16 Onboarding (in-process sub-module of dis-ui-server)
**What it is.** An in-process sub-module of dis-ui-server (previously a separate `onboarding-service`).
**Role.** Same as before: turn a sample into a draft mapping config and validation suite. Accepts a sample, infers source schema (DuckDB), suggests field-by-field mapping and normalization rules, proposes both validation suites. Persists drafts to `config.source_mappings` with `status='staged'` on operator approval. Coordinates the staged → active promotion after shadow rollout review.
**Why merged.** dis-ui-server was the only caller; CPU profile of sample inference does not warrant process isolation. If onboarding work later blocks BFF latency, the sub-module structure (inference/, suggestion/, validation_draft/, shadow/) is designed for clean extraction.

### 4.17 dis-ui-server (BFF for DIS UI)
**What it is.** The single backend-for-frontend service the DIS UI calls. Per-sub-module handlers; one Customer Master integration; never writes to canonical.
**Role.** Serves every read and write the DIS UI needs. Reads from Cloud SQL read replica, config schema, quarantine schema, Cloud SQL `audit.events` (BigQuery `audit_events` from Phase 3 onward). Writes only to `config.source_mappings` (mapping CRUD, onboarding promotion); publishes `ingress.resubmit` (resubmit action from quarantine console) and `mapping.changed` (mapping CRUD and promotions). Hosts the onboarding sub-module in-process (`decisions.md` D16). Hosts the **upload-session endpoint** that issues signed PUT URLs to start a CSV upload from the UI: validates the session, generates `trace_id`, builds the canonical GCS path, returns the signed URL, emits audit (`decisions.md` D36). The UI never calls a separate receiver service to start an upload.
**Handlers.** sample_upload, onboarding_review, mapping_crud, quarantine, audit, duckdb_query, upload_session, auth (cross-cutting FastAPI dependency).
**Why one BFF.** One Customer Master integration. One URL for the UI. Per-domain APIs multiply auth integrations and deploy units for no v0 benefit. See `decisions.md` D26 and D36.

### 4.18 PII Tokenization Module (lib, not a service)
**What it is.** A library (`libs/dis-pii`) used by every receiver. HMAC-based deterministic tokenization with per-tenant keys; key vault; field-name and pattern-based PII detection; per-source policy.
**Role.** Tokenize phone, email, loyalty_id, PAN, Aadhaar, and tenant-policy fields before any persistence. Receivers call into this lib in the enrichment stage; bronze and canonical never see raw PII.
**Why a library, not a service.** Every receiver needs it; calling a service adds latency to the hot path. Tokenizer logic and key handling are stable enough to ship as code dependencies, not network dependencies. See `decisions.md` D24.

### 4.19 Quarantine Drainer
**What it is.** A containerized Pub/Sub consumer subscribed to the `quarantine` topic.
**Role.** Persist quarantine messages from streaming-consumer (and receiver preflight) into the `quarantine.*` Cloud SQL tables with RLS-aware writes. Surface to dis-ui-server (and through it to the DIS UI quarantine console) as queryable rows.
**Why a separate service.** Different runtime profile from the streaming consumer (low volume, latency-tolerant). Different DB write pattern (per-row inserts into quarantine schema, not batched canonical commits). Isolating it keeps the streaming consumer focused on the hot path.

### 4.20 Daily Compute Job
**What it is.** A scheduled containerized job, triggered by Cloud Scheduler in retail off-hours. Runs Postgres-local against the Cloud SQL canonical schema.
**Role.** Computes daily derived attributes per SKU and persists them. Two outputs:
1. INSERT into `canonical.store_sku_signal_history` (today's row per SKU, partitioned by as_of_date).
2. UPDATE `canonical.store_sku_current_position` with today's computed values (velocity_7day, stock_age_days, unit_cost_trend_30day; future signals as added).
**Read path (incremental).** For each SKU:
- Read yesterday's row from `store_sku_signal_history`.
- Read today's events from `store_sku_sale_events` and `store_sku_change_events` (within the 24h buffer).
- Combine: yesterday's value + delta from today's events = today's value.
**Read path (bootstrap/recovery).** When yesterday's row is missing (day 1, or after a missed compute day): read the full window from BigQuery (`canonical_history.*`). Slower, but recovers correctness.
**Per-tenant execution.** Sets `app.tenant_id` per tenant; processes tenants sequentially or in small parallel batches. Avoids cross-tenant data leakage even though the table doesn't enforce RLS for compute writes.
**Idempotency.** UNIQUE constraint on `(tenant_id, store_id, sku_id, sku_variant, sku_lot_batch, as_of_date)` prevents duplicate signal_history rows. UPDATE on current_position is safe to re-run.
**Why it exists.** ROOS reads derived attributes from `store_sku_current_position` continuously; the compute job keeps them fresh daily. Signal history preserves daily values for backtesting and incremental compute.
**Why Postgres-local.** Critical-path independence from BigQuery. Predictable latency. Per-tenant isolation via RLS context. See `decisions.md` D31.

## 5. Source Onboarding

A new source mapping enters `config.source_mappings` via an assisted onboarding flow, not by direct DB insertion. The flow combines automated proposal (schema inference, mapping suggestion, validation suggestion) with mandatory human approval and staged rollout.

### 5.1 Onboarding flow

```
   ┌─────────────────── SOURCE ONBOARDING (assisted) ──────────────────────────┐
   │                                                                           │
   │   DIS UI: Sample upload sub-module                                        │
   │   (tenant or Ithina ops uploads sample file/payload)                      │
   │                          │                                                │
   │                          ▼                                                │
   │   ┌─────────────────────────────────────────────────────────────┐         │
   │   │  Onboarding Service (containerized service)                 │         │
   │   │                                                             │         │
   │   │  Layer 1: SCHEMA INFERENCE                                  │         │
   │   │    DuckDB reads sample from GCS:                            │         │
   │   │      DESCRIBE SELECT * FROM read_csv('gcs://...')           │         │
   │   │    -> inferred columns, types, null %, sample values        │         │
   │   │                                                             │         │
   │   │  Layer 2: MAPPING + NORMALIZATION SUGGESTION                │         │
   │   │    Match source columns -> canonical columns via:           │         │
   │   │      - exact name match                                     │         │
   │   │      - fuzzy match (string similarity)                      │         │
   │   │      - value-pattern match (regex/heuristics)               │         │
   │   │      - historical mappings (similar source types)           │         │
   │   │    Detect format variance from sample:                      │         │
   │   │      - date format (DD-MM vs MM-DD inferred from values)    │         │
   │   │      - decimal separator (. vs ,)                           │         │
   │   │      - boolean/null encodings, enum value sets              │         │
   │   │      - timezone (store-local default), units                │         │
   │   │    -> draft field_map + draft transforms + confidence       │         │
   │   │                                                             │         │
   │   │  Layer 3: VALIDATION SUGGESTION                             │         │
   │   │    Profile sample -> propose expectations:                  │         │
   │   │      - not-null where sample is 100% populated              │         │
   │   │      - range bounds from min/max                            │         │
   │   │      - regex from value patterns                            │         │
   │   │    -> draft expectation suite                               │         │
   │   └──────────────────────┬──────────────────────────────────────┘         │
   │                          │                                                │
   │                          ▼                                                │
   │   ┌─────────────────────────────────────────────────────────────┐         │
   │   │  DIS UI: Onboarding review sub-module                       │         │
   │   │    - proposed mapping shown side-by-side with sample data   │         │
   │   │    - low-confidence rows highlighted                        │         │
   │   │    - operator can override, add transforms, mark column     │         │
   │   │      ownership (which canonical columns this source owns)   │         │
   │   │    - dry-run against sample -> canonical preview            │         │
   │   │    - approve -> writes to config.source_mappings            │         │
   │   │      with status='staged'                                   │         │
   │   └──────────────────────┬──────────────────────────────────────┘         │
   │                          │                                                │
   │                          ▼                                                │
   │   ┌─────────────────────────────────────────────────────────────┐         │
   │   │  Staged rollout (shadow mode)                               │         │
   │   │    - pipeline processes incoming chunks against the staged  │         │
   │   │      mapping in parallel, writes preview rows to a          │         │
   │   │      staging.* schema, NOT canonical                        │         │
   │   │    - operator reviews shadow output via DIS UI: Onboarding  │         │
   │   │      review sub-module (promotion screen)                   │         │
   │   │    - approve -> status='active', mapping joins live config  │         │
   │   │    - reject -> status='deprecated', operator iterates       │         │
   │   └─────────────────────────────────────────────────────────────┘         │
   │                                                                           │
   └───────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Steps

1. **Sample upload.** Operator (or tenant) uploads a sample file or pastes a sample payload via the DIS UI's *Sample upload* sub-module. The DIS UI calls the Onboarding Service backend, which stores the sample in GCS under an onboarding-staging path.
2. **Schema inference (Layer 1).** DuckDB inspects the sample, returning inferred column names, types, null percentages, value distributions, and a handful of example values per column.
3. **Mapping suggestion (Layer 2).** The service matches each inferred source column against the canonical schema using exact-name match, fuzzy string similarity, value-pattern heuristics (looks-like-SKU, looks-like-price, looks-like-timestamp), and historical mappings approved for similar source types. It also proposes **normalization rules** by detecting format variance in the sample: date format (e.g., consistent `DD-MM-YYYY` based on values where the first pair exceeds 12), decimal separator, boolean encoding, enum value set, timezone heuristics (store-local default), units. Output: a draft `field_map` with a confidence score per column and a draft `transforms` entry per column where normalization is needed.
4. **Validation suggestion (Layer 3).** The service profiles the sample and proposes validation rules (or GE/Pandera schemas, if adopted), producing **two draft suites**: a *source-shape* suite (required source columns, source-level types, null patterns, encoding) checked before mapping, and a *canonical-shape* suite (canonical column invariants, range bounds, cross-field consistency) checked after mapping. Both are versioned together with the mapping.
5. **Operator review.** The DIS UI's *Onboarding review* sub-module shows the proposed mapping side-by-side with the sample, with low-confidence rows highlighted. The operator can override mappings, add custom transforms, and declare which canonical columns this source is authoritative for (required for the column-scoped merge in `decisions.md` D8). A dry-run renders a canonical preview from the sample.
6. **Approval to staged.** On approval, the mapping is written to `config.source_mappings` with `status='staged'` and a new version. It is not yet used for canonical writes.
7. **Shadow rollout.** The Dataflow pipeline processes incoming chunks for this (tenant, source) through the staged mapping in parallel with any existing active mapping, writing the staged output to a `staging.*` schema rather than `canonical.*`. Operator reviews the staged output via the DIS UI's *Onboarding review* sub-module (promotion screen) against expectations.
8. **Promotion to active.** Operator promotes the mapping: `status='active'`. The previous active mapping (if any) moves to `status='deprecated'`. The pipeline picks up the change at the next side-input refresh (~30s).
9. **Rejection or iteration.** If the staged output is wrong, the mapping is marked `deprecated` and the operator iterates on a new version.

### 5.3 What "tenant onboarding" means versus "source onboarding"

These are distinct:

- **Tenant onboarding** is an admin-app concern: create the tenant record, provision auth credentials, register stores. No data flows yet. Outside the scope of the data platform architecture.
- **Source onboarding** is what §7 covers: registering and validating the mapping from a specific data source (a tenant's POS, ERP, or CSV feed) into the canonical schema. A single tenant may onboard multiple sources over time; each goes through this flow independently.

### 5.4 Self-serve vs ops-driven

The same flow supports both. Differences are UI-level:

- **Ops-driven** (common case, especially for new source types or complex tenants): Ithina ops uploads the sample, reviews the proposed mapping, and approves on the tenant's behalf, possibly with tenant input on column ownership and business rules.
- **Self-serve** (for known source types and confident tenants): the tenant uploads their own sample via the DIS UI's *Sample upload* sub-module, reviews the proposed mapping via the *Onboarding review* sub-module, and approves. Ithina ops may have a final approval gate before promotion to active, depending on the tenant's tier.

### 5.5 Quality of suggestions

The auto-suggester's accuracy is the gating factor on onboarding throughput:

- **Cold start (rule-based only):** ~60-75% of columns matched correctly on first try, for common source types (Shopify, Square, NetSuite). Edge cases and customized columns need operator input.
- **With historical learning:** ~75-85% as the platform accumulates a corpus of approved mappings to match against.
- **With LLM-assisted semantic matching (Vertex AI Gemini):** ~85-95% achievable for unfamiliar schemas, at additional infra cost and a feedback loop to maintain.

Recommendation for v0: rule-based suggester only. Add historical learning once 20+ mappings are in the corpus. Add LLM assistance only if onboarding throughput becomes a measurable bottleneck.

## 6. Data Flow

### 6.1 Hot path (event from store to canonical)

1. **Source emits event** (POS sale, inventory change, ERP CSV chunk, partner webhook, or external API response that Ithina is pulling).
2. **Receiver accepts.** Authenticates the caller, generates `trace_id`, calls Identity Service `resolve()` to obtain `tenant_id`, `store_id`, and metadata. For CSV channels (manual upload and per-tenant ERP POST endpoint), runs pre-flight (basic checks, or DuckDB stats query if Option B is adopted) to reject obviously malformed files at the boundary.
3. **Receiver persists.** Writes the raw payload to GCS (one blob per ingress chunk), writes one enriched chunk row to bronze Postgres, and publishes a message to `ingress.ready` carrying `{trace_id, tenant_id, store_id, source_id, bronze_ref, gcs_uri, received_ts}`. Returns 2xx to caller.
4. **Dataflow consumes** the message. Fetches the chunk (from bronze PG or GCS, whichever is canonical for that channel). Looks up the per-(tenant, source) mapping and the two validation suites (source-shape, canonical-shape) in `config.source_mappings` via a refreshing side input. Calls Identity Service `validate()` to confirm tenant/store are still active (catches deletes between receive and process).
5. **Pre-mapping validation (source-shape).** Runs the source-shape suite against the chunk in its raw, pre-mapped form. Checks: required source columns present, no excessive nulls, source-level type sniff, row count within plausible bounds, encoding sanity. Failure here is typically chunk-level and routes the whole chunk to quarantine with a "source shape" reason, intelligible to the tenant ("expected column `item_code`, got `itemcd`"). No compute is wasted mapping a malformed chunk.
6. **Beam applies the mapping** in four sub-stages: **rename** (source field name → canonical field name), **normalize** (parse and canonicalize representation: date formats, decimal separators, timezones, units, enums, booleans, nulls, casing, whitespace, per the `transforms` field in the mapping config), **cast** (string → target type, now safe because normalize produced canonical representations), **derive** (computed fields from others). The mapping's declarative `transforms` vocabulary handles common normalizations; a named custom transform function (escape hatch) handles source-specific quirks the vocabulary can't express. Normalization failures (ambiguous or unparseable values) route to quarantine with a distinct "normalization failed" reason: column, value, expected format. The chunk is exploded into N canonical row candidates only for rows that pass normalization and cast.
7. **Post-mapping validation (canonical-shape).** Runs the canonical-shape suite against each mapped row. Checks: canonical required columns populated, numeric range bounds (`inventory_units >= 0`, `price >= 0`), `event_ts` plausibility, regex on identifiers (`sku_id`), cross-field invariants (`is_returned = true` implies `quantity < 0`), and a pre-check that `tenant_id` and `store_id` exist in `identity_mirror` (avoids a wasted DB round-trip to fail FK). Failure here is per-row; failing rows go to quarantine, passing rows continue.
8. **Branch.** Valid rows fan out to two sinks: the canonical hot table (column-scoped merge upsert, event-time conditional) and the canonical history table (unconditional append), both inside a single RLS-aware transaction grouped by `tenant_id`. Invalid rows route to the `quarantine` Pub/Sub topic.
9. **Audit emit.** Every stage (read, fetch, source-shape validate, map, canonical-shape validate, sink) emits a structured row to Cloud SQL `audit.events` in Phase 1 (BigQuery `audit_events` from Phase 3 onward; see `decisions.md` D34), keyed by `trace_id`, with stage, status, timestamp, row count, and any error context.
10. **Quarantine sink** drains its Pub/Sub topic into the Cloud SQL `quarantine` table, with the raw payload pointer, suite failure reasons (if GE/Pandera is adopted, including which suite failed: source-shape vs canonical-shape), and links back to the source bronze chunk. The tenant-facing UI reads from this table.

### 6.2 ELT outputs: how Beam writes to its sinks

The pipeline emits to four sinks. Their delivery semantics differ; this section makes them explicit.

```
                     Beam pipeline (per chunk, per row)
                                 │
        ┌────────────────────────┼─────────────────────────┐
        │                        │                         │
   ┌────▼────┐             ┌─────▼─────┐             ┌─────▼─────┐
   │ valid   │             │ invalid   │             │ audit     │
   │ rows    │             │ rows /    │             │ events    │
   │         │             │ failed    │             │ (every    │
   │         │             │ chunks    │             │  stage,   │
   │         │             │           │             │  every    │
   │         │             │           │             │  row,     │
   │         │             │           │             │  not      │
   │         │             │           │             │  gated)   │
   └────┬────┘             └─────┬─────┘             └─────┬─────┘
        │                        │                         │
        │ batch by tenant_id     │ publish to              │ streaming
        │ (~500 rows / batch)    │ Pub/Sub topic           │ insert
        ▼                        │ 'quarantine'            ▼
   ┌─────────────────────┐       │                  ┌──────────────┐
   │ One RLS-aware       │       ▼                  │ Cloud SQL    │
   │ transaction per     │  ┌──────────────┐        │ audit.events │
   │ tenant batch:       │  │ Quarantine   │        │ (Phase 1)    │
   │                     │  │ drainer      │        │              │
   │  BEGIN              │  │ (subscriber) │        │ partition by │
   │  SET LOCAL          │  └──────┬───────┘        │   date       │
   │   app.tenant_id     │         │                │ archived to  │
   │   = '<id>'          │         │ batch          │ BigQuery in  │
   │                     │         │ insert         │ Phase 3      │
   │                     │         │                └──────────────┘
   │  -- hot upsert:     │         ▼
   │  INSERT INTO        │  ┌──────────────┐
   │   canonical.<hot>   │  │ Cloud SQL    │
   │   ON CONFLICT ...   │  │ quarantine   │
   │   WHERE event_ts    │  │ table (RLS)  │
   │   > existing.ts     │  │              │
   │                     │  │ - tenant_id  │
   │  -- history append: │  │ - trace_id   │
   │  INSERT INTO        │  │ - failure    │
   │   canonical.<hist>  │  │   reason     │
   │                     │  │ - payload    │
   │  COMMIT             │  │   pointer    │
   │                     │  │ - suite ref  │
   │  (FK to             │  │   (if GE/    │
   │   identity_mirror   │  │    Pandera)  │
   │   enforced;         │  └──────────────┘
   │   FK failure ->     │         │
   │   retry-backoff,    │         │ read
   │   then quarantine)  │         ▼
   └─────────────────────┘  ┌──────────────┐
              │             │ DIS UI       │
              │             │ Quarantine   │
              │             │ console      │
              │             │ (tenant +    │
              │             │  ops slices) │
              │             └──────────────┘
              ▼
   ┌──────────────────────────┐
   │ Cloud SQL canonical      │
   │  - hot tables (RLS)      │
   │  - history tables (RLS)  │
   └──────────────────────────┘
              │
              │ (nightly batch, separate path)
              ▼
   ┌──────────────────────────┐
   │ BigQuery                 │
   │ canonical_history.*      │
   │ (see §6.4 cold path)     │
   └──────────────────────────┘
```

**Key properties of each output path:**

| Sink | Trigger | Batching | Delivery semantics | Transactional? |
|---|---|---|---|---|
| Canonical hot table | Per valid row, per tenant batch | ~500 rows / tx | Exactly-once via deterministic key + event-time conditional upsert | Yes, same tx as history |
| Canonical event table | Per valid row, per tenant batch | ~500 rows / tx | Append-only; duplicates per `(tenant_id, store_id, source_id, source_event_id)` recorded as separate rows; latest-wins at read time (see §2.3.2) | Yes, same tx as hot |
| Quarantine | Per invalid row (post-mapping) or per failed chunk (pre-mapping) | Per-message | At-least-once via Pub/Sub; drainer is idempotent on `trace_id`+`row_hash` | No (best-effort) |
| Audit events | Per stage, per row, every row (not gated by valid/invalid) | Streaming insert (bundled) | At-least-once; idempotent in BQ via insertId | No |

**Notes:**
- Hot and history writes are **always in the same Postgres transaction** for a given tenant batch. A row cannot land in hot without also landing in history. Tenant ordering inside a batch is by `event_ts`.
- BigQuery `canonical_history` is **not written from the streaming pipeline.** It is loaded nightly by the batch job (§6.4), reading the Cloud SQL history slice. This keeps the hot-path latency budget free of BQ write variance.
- `audit.events` writes (Phase 1, Cloud SQL) are fire-and-forget and parallel to the main flow. A delay or partial failure on audit emission never blocks canonical writes; audit gaps are logged and re-emitted from bronze on demand.
- The quarantine drainer is a separate small process (see `decisions.md` D13 backend) that reads from the `quarantine` Pub/Sub topic and writes to the Cloud SQL `quarantine` table; the DIS UI reads from that table.

### 6.3 Identity path (out-of-band)

- Admin app writes go through the Identity Service. On any tenant/store change (create, update, soft-delete), the Identity Service publishes `identity.changed`.
- Two subscribers consume:
  - The Identity Service's own cache invalidator (evicts cache keys).
  - The Mirror Sync Consumer (upserts or marks inactive in `identity_mirror.tenants` / `identity_mirror.stores`).
- Canonical tables have real Postgres FKs to the mirror tables. Inserts referencing a tenant/store not yet replicated fail with FK violation; Beam sink retries with exponential backoff before quarantining as a last resort.
- TTL on the identity cache (5-15 min) is the safety net if Pub/Sub lags.

### 6.4 Cold path (nightly BQ offload and Cloud SQL eviction)

Triggered by Cloud Scheduler during the no-ingress window (retail off-hours).

1. Identify yesterday's slice in `canonical_history` (watermark column).
2. **Optional validation gate (Option B):** run a GE or Pandera suite on the slice. On failure, hold the load and alert ops.
3. **Load to BigQuery.** A containerized job (or Dataflow batch) writes the slice to `bigquery.canonical_history.*` using the Storage Write API. Idempotent: re-running the same slice is a no-op due to deterministic row keys.
4. **Evict from Cloud SQL.** Delete rows in `canonical_history` with `event_ts < now() - retention_window` (default 35 days, configurable per `decisions.md` D29), in batched chunks to avoid long locks. BigQuery already has them from prior nights.
5. The hot tables are not affected; they continue serving current-state reads from Cloud SQL.

### 6.5 Replay paths

- **Tenant resubmit (replay flavor).** Operator clicks "retry" on a quarantined chunk in the DIS UI quarantine console. dis-ui-server publishes `ingress.resubmit` with `resubmit_type=replay`, `parent_trace_id` linking the original, and a new `trace_id`. Streaming consumer re-processes the same bronze payload through the mapping version recorded on the original (B1, `decisions.md` D22) — *not* current active, unless ops explicitly overrides.
- **Tenant resubmit (fixed_file flavor).** Tenant uploads a corrected file via the DIS UI Sample Upload, indicating it replaces a quarantined chunk. The receiver creates a fresh bronze row + GCS object and publishes `ingress.resubmit` with `resubmit_type=fixed_file`, `parent_trace_id`, and `chain_depth` incremented. Streaming consumer processes against current active mapping (the fix was in the data, not the mapping).
- **Ithina-side replay.** Ops triggers a re-publish of selected bronze chunks via `tools/replay/`. Publishes `ingress.resubmit` with `initiated_by=ops`, `resubmit_type=replay`. Pinned to the original mapping version by default; ops can override to current active when investigating a mapping bug that was just fixed. Used after a mapping bug fix to reprocess historical chunks; audit trail surfaces both versions in dispute scenarios.
- **Chain-depth cap.** `chain_depth` capped at 3. Resubmit messages exceeding the cap are rejected by the publisher; chunks needing further retries become an ops-managed escalation.
- **Hot-tier event-time logic** ensures replayed rows do not overwrite newer state in the hot tier; history still receives the replayed row stamped with its `mapping_version_id`.

## 7. Tenant Data Isolation

- **RLS on every canonical, history, and quarantine table.** No `BYPASSRLS`. The streaming consumer's canonical sink batches rows by `tenant_id`, opens a transaction, sets `SET LOCAL app.tenant_id = '...'`, runs the writes, commits.
- **Identity store in Customer Master** (Sevyn8's Auth0-integrated identity system, physically separate from the data-platform DB), accessed only through the Identity Service. The data platform never holds Customer Master credentials.
- **FK substitute via mirror table.** Real Postgres FKs from canonical to `identity_mirror`. Mirror is maintained by a dedicated consumer; no app writes it directly.
- **Audit events partitioned and clustered by tenant_id** in BigQuery, queryable per tenant.

## 8. Audit and Traceability

- `trace_id` is generated at the receiver and propagated through every downstream message and storage row.
- Every Beam stage emits one audit event per row: `{trace_id, tenant_id, store_id, source_id, stage, status, ts, row_hash, error_code, error_detail}`.
- Debugging a problem row: `SELECT * FROM audit.events WHERE trace_id = '...' ORDER BY event_timestamp;` returns the full lifecycle.
- Quarantine rows link back to the bronze chunk and the validation failure context (when GE/Pandera is adopted).

## 9. Open Questions

### 9.1 Blocker-grade

These three questions are not "nice to have" deferrals. Each represents a decision that is silently wrong now and becomes vastly more expensive to fix once data accumulates or tenants multiply.

- **B1 (RESOLVED) · Mapping version pinning on every canonical row.** See `decisions.md` D22. Every canonical row carries `mapping_version_id BIGINT NOT NULL`. Replay defaults to original-version pin; ops can override with audit trail.
- **B2 (OPEN) · Normalization failure granularity.** Pre-mapping is chunk-level; post-mapping is per-row; normalization sits between them and granularity is undefined. Three plausible answers: chunk-level fail (tenant-hostile, ops-friendly), per-row fail (silent-wrong risk when format inference is wrong for the whole chunk), per-column-per-chunk inference (most correct, most complex). The schema accommodates either decision (see `quarantine.granularity` in `contracts/pubsub/`). Blocks streaming-consumer implementation; needs decision before that service is built.
- **B3 (OPEN) · Performance isolation between tenants.** Today's "shared pipeline with fairness mechanisms" defers the actual mechanism. Options: hard per-tenant isolation (separate service instances per tenant, expensive); soft fairness (token-bucket rate limit at receivers, fair-share scheduling at streaming consumer); tiered (paid tier gets isolation, others share). Blocks v1.0 launch readiness; needs explicit defense for the first noisy tenant scenario.

### 9.2 Lower-priority, non-blocker

These are deferred design decisions that do not block v1.0 but should be settled before they accumulate cost:

- **Bronze granularity.** One row per ingress chunk (metadata-only) is settled. Within-chunk granularity for resubmit purposes (e.g., resubmit a single row from a chunk) remains open.
- **History tier grain.** One row per cleaned event (default), or per-attribute deltas, or chunk-level snapshot.
- **Multi-source precedence.** When two sources both write the same canonical column, which one wins. Likely a per-column rule in the mapping config.
- **Late-arriving history rows** after the Cloud SQL retention eviction window: where do they land?
- **DIS UI quarantine console capabilities.** Read + resubmit are v1.0; edit-and-replay (in-line correction of a quarantined row before resubmit) is post-v1.0.
- **Trace-level dedup for retry compute savings.** Re-processing the same `trace_id` chunk currently runs the full pipeline (mapping + validation + canonical write) even though source-event-id dedup catches it at the audit layer as `DUPLICATE_NOOP`. At higher scale, consider checking for prior `CANONICAL_WRITTEN` audit events at streaming-consumer entry and skipping the chunk if found. Adds an audit read per chunk; saves the rest. Not worth the machinery at beta scale.

## 10. Glossary

| Term | Meaning |
|---|---|
| Bronze | Metadata-only index in Cloud SQL referencing raw payloads in GCS. Payloads remain durable in GCS. |
| Canonical | The semantically normalized model: hot tier (current state) + history tier (event log). Every canonical row carries `mapping_version_id` (B1, `decisions.md` D22). |
| Hot table | Canonical table holding current-state rows, upserted on every event. |
| History table | Canonical append-only table recording every cleaned event. |
| RLS | Row-Level Security; Postgres feature enforcing per-tenant row visibility. |
| Trace ID | Unique identifier generated at the receiver for every ingress chunk, propagated end-to-end. |
| Mapping | Per-(tenant, source) declarative configuration that translates source fields into canonical columns. Versioned. |
| `mapping_version_id` | Identifier of the mapping version that produced a given canonical row. Pinned per row per B1 (`decisions.md` D22). Replay defaults to the pinned version, not current active. |
| Mirror table | Locally replicated subset of Customer Master identity data, used to enforce FK in the data-platform DB. |
| Quarantine | Pub/Sub topic + Cloud SQL schema + UI surface for ingress data that failed semantic validation. |
| DLQ | Dead Letter Queue. `pipeline.dlq` topic holds batches diverted by the streaming consumer when Cloud SQL is unhealthy. Distinct from quarantine (data failures vs infrastructure failures). |
| Pre-flight | Cheap structural check at the receiver before accepting a CSV. |
| Source-shape validation | Pre-mapping checks on the raw chunk (required columns, types, encoding). Pandera. |
| Canonical-shape validation | Post-mapping checks on canonical row invariants (ranges, business rules, FK pre-checks). Pandera. |
| Normalization | Sub-stage of mapping that canonicalizes representation (date formats, decimal separators, timezones, units, enums, booleans, nulls, casing, whitespace) before cast. Declarative transforms with custom-function escape hatch. |
| Pandera | Declarative data-validation library. The validation engine. |
| Containerized service | Long-running application packaged as a container; deployment platform is implementation choice. |
| Containerized job | One-shot or scheduled application packaged as a container. |
| **Streaming Consumer** | The ELT core. Containerised service running a Pub/Sub-pull subscriber loop. Specific runtime is a deployment choice; migration trigger for higher-throughput runtimes per `decisions.md` D4. |
| **DIS** | Data Integration System. Synonym for "Ithina Data Platform." Also the prefix for `dis-ui-server`, `dis-ui`, `libs/dis-*`. |
| **DIS UI** | The single containerized front-end hosting all user-facing screens. Auth via Customer Master. Calls one backend: dis-ui-server. |
| **dis-ui-server** | The BFF (backend-for-frontend) for the DIS UI. Single service, per-sub-module handlers, hosts the onboarding sub-module in-process. |
| **BFF** | Backend-for-frontend. A single backend service tailored to one UI consumer, as opposed to per-domain APIs. See `decisions.md` D26. |
| **Customer Master** | Sevyn8's Auth0-integrated identity, auth, and RBAC system. External to DIS; the single source of truth for user identity across Sevyn8 products. |
| **PII tokenization** | HMAC-based deterministic tokenization with per-tenant keys, applied at receivers before any persistence. Phone, email, loyalty_id, PAN, Aadhaar, and tenant-policy fields. See `decisions.md` D24. |
| **dbt** | Data Build Tool. Scoped to BigQuery in DIS: `canonical_history.*` models, data tests via `dbt-expectations`. Postgres schema migrations use Alembic instead. See `decisions.md` D23. |
| **Alembic** | Python-based schema migration tool. Used for every DIS Postgres schema: canonical, config, bronze, identity_mirror, quarantine, staging. Same tool used in Customer Master. See `decisions.md` D23. |
| **Atomic dual-write** | The streaming consumer's pattern of writing both the hot table upsert and the matching event-table insert in a single Cloud SQL transaction. Either both succeed or both roll back. See `decisions.md` D30. |
| **DUPLICATE_NOOP / DUPLICATE_OVERWRITTEN** | Audit outcomes recorded when a new event arrives with the same `(tenant_id, store_id, source_id, source_event_id)` as a prior event. NOOP = identical canonical payload (typical retry). OVERWRITTEN = different payload (correction from the source). See §2.3.3, `decisions.md` D33. |
| **Signal history** | `canonical.store_sku_signal_history`. Append-only daily history of computed derived attributes per SKU per as_of_date. Source of incremental daily compute. See `decisions.md` D32. |
| **Daily compute job** | Postgres-local scheduled job that computes derived attributes per SKU each day. Reads yesterday's signal_history row + today's events; writes today's signal_history row + updates store_sku_current_position. See `decisions.md` D31, D20. |
| **Event buffer** | Cloud SQL retention window for events and signal history. Default 35 days; configurable via the eviction job's retention parameter. BigQuery archives from day 1. |
| **BqClient** | The libs/dis-core wrapper around google-cloud-bigquery. Auto-injects `WHERE tenant_id = :tenant_id` on every query. CI lint forbids direct google-cloud-bigquery usage in services. |
| **INGRESS_EVENT / ROW audit scope** | Audit-event scope distinguisher. INGRESS_EVENT-scoped events are per-stage summary for one chunk; ROW-scoped events are per-row records (typically failures). Volume scales with failure rate, not row count. |
| **EPE** | Entry / Process / Exit. A per-service mandate format used in the DIS Engineering Reference. Each service has its EPE block specifying triggers, ordered process, and durable outputs. |
| **B1, B2, B3** | Blocker-grade open questions. B1 (mapping version pinning) is resolved. B2 (normalization granularity) and B3 (performance isolation) remain open. See §9.1. |

---

## Revision History

- **v0.9.** Refactor: design decisions extracted to `decisions.md`; component inventory and storage layout moved to `engineering-reference.md`. Beta-scale right-sizing (5 tenants × ~25 stores × 5000 SKUs; ~150K events/day). Cloud SQL retention raised from 24h to 35 days (configurable); daily Cloud SQL → BigQuery copy continues, eviction drops only partitions older than retention.
- **v0.8.** History-tier sizing pinned; atomic dual-write pattern; new `canonical.store_sku_signal_history` table for daily-computed derived attributes; incremental Postgres-local daily compute pattern.
- **v0.7.** Alembic adopted for DIS Postgres schema migrations; dbt scoped to BigQuery analytics only.
- **v0.6.** B1 resolved (mapping version pinned on canonical rows).
- **v0.5.** dis-ui-server added as BFF for DIS UI.
- **v0.4.** B1, B2, B3 raised.
- **v0.3.** Customer Master named, PII redaction at receiver, latency SLO formalized, resubmit mechanics, backpressure + DLQ, Identity Service stale-while-error.
- **v0.2.** Auth and RBAC scoped to Customer Master.
- **v0.1.** Cloud Run for streaming v0.1, Pandera selected.
