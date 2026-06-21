# Worked Example — Streaming Consumer

**Audience:** human operator. Reference layout of `services/streaming-consumer/`, the ELT pipeline service. Used when designing future slices for this service or auditing its structure.

**Status:** reference. Not auto-loaded by Claude Code. Slice docs in `docs/slices/` drive Claude Code; this doc is one possible reference shape for the largest service in DIS.

**Companion docs.**
- `architecture.md` — system rationale.
- `decisions.md` — D4 (streaming runtime), D22 (mapping version pinning), D30 (atomic dual-write), D33 (event-table dedup).
- `engineering-reference.md` — repo overview.
- `services/streaming-consumer/README.md` — current EPE (Entry / Process / Exit) block.

---


This service is detailed first because it is the most internally complex of the ten services; the others, detailed in §4, follow simplified versions of the same shape. The conventions established here (folder names, what belongs where, what does not) apply across the codebase.

`services/streaming-consumer/` is the ELT pipeline. It runs as a containerised service. The migration trigger to a higher-throughput runtime is sustained 500+ rows/sec for 7 days. See decisions.md D4. The transformation logic is factored as pure functions over `(mapping, raw_row) → canonical_row` so the migration is a runner swap, not a rewrite.

**Purpose.** Consume `ingress.ready` messages, fetch the corresponding bronze chunk, apply the per-(tenant, source) mapping, validate both pre- and post-mapping, and land valid rows in canonical (hot + history) and invalid rows in quarantine. Emit audit events throughout.

**Entry.**
- Trigger: Pub/Sub message on `ingress.ready` subscription. Ordering key: `tenant_id`.
- Upstream producers: receivers (§§4.1, 4.2, 4.3, 4.4). In v1.0 only §4.2 csv-upload publishes.
- Inputs: message envelope `{trace_id, tenant_id, store_id, source_id, bronze_ref, gcs_uri, received_ts, replay?}`; side-inputs (refreshing cache): mapping config from `config.source_mappings` (written by §4.10 dis-ui-server mapping-CRUD handler) and `identity_mirror` (maintained by §4.6 mirror-sync-consumer).
- Preconditions: Cloud SQL canonical schema reachable; Identity Service (§4.5) reachable for `validate()` calls (or `identity_mirror` available as fallback); mapping config exists for `(tenant_id, source_id)` (else routes whole chunk to quarantine).

**Process.**
- Receive message; ack-extend during processing.
- Fetch the chunk from bronze metadata + GCS (architecture step 2).
- Look up the mapping spec for `(tenant_id, source_id)` from the side-input cache (architecture step 3).
- Validate FK existence: tenant + store present and active in `identity_mirror` (architecture FK pre-check, step 4); on cache miss, call §4.5 `identity-service.validate()`.
- Run pre-mapping validation (source-shape Pandera suite, step 5); chunk-level failures route the entire chunk to quarantine.
- Apply the four normalization sub-stages: rename → normalize → cast → derive (step 6).
- Run post-mapping validation (canonical-shape Pandera suite, step 7); per-row failures route rows to quarantine.
- Branch valid vs invalid rows (step 8); valid rows continue to canonical sinks, invalid rows publish to `quarantine` topic.
- Canonical sink (atomic dual-write per architecture §4.30): open RLS-aware transaction per tenant batch (~500 rows); for each canonical row, UPSERT into `store_sku_current_position` (event-time conditional, column-scoped merge) AND INSERT into the matching event table (`store_sku_sale_events` for sale events; `store_sku_change_events` for everything else); commit. Either both writes land or both roll back. FK failures retry with backoff, then quarantine.
- Cloud SQL circuit-breaker: probe `SELECT 1` before each batch; on unhealthy, route to `pipeline.dlq` instead of committing.
- Emit audit events at every stage (per stage, per row, not gated by valid/invalid).
- Ack message on canonical-tx commit (or on DLQ publish during circuit-open).

**Exit.**
- Success: valid rows landed atomically in canonical hot + event tables (single transaction per tenant batch); invalid rows published to `quarantine` topic (consumed by §4.8 quarantine-drainer); per-stage per-row audit events emitted (read by §4.10 dis-ui-server audit handler); message acked.
- Failure modes handled: FK violation → retry-with-backoff → quarantine; Cloud SQL transient error → batch retry; Cloud SQL unhealthy → route to `pipeline.dlq` and trigger receiver-side 503s; mapping config missing → entire chunk to quarantine with `mapping_not_found` reason.
- Failure modes propagated: persistent Pub/Sub or Cloud SQL outage → nack and Pub/Sub retries; ops alerted; DLQ accumulates until health restores.
- Edge cases: out-of-order events (older `event_ts` than canonical hot) → no-op upsert (history still appends); replay of an already-processed chunk → idempotent via `trace_id` + row hash in hot upsert and history-append constraints.

### 3.1 Full layout

```
services/streaming-consumer/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── streaming_consumer/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       │
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── fetch.py
│       │   ├── mapping.py
│       │   ├── normalize.py
│       │   ├── validate_pre.py
│       │   ├── validate_post.py
│       │   └── branch.py
│       │
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── canonical.py
│       │   ├── quarantine.py
│       │   ├── dlq.py
│       │   └── audit.py
│       │
│       ├── health/
│       │   ├── __init__.py
│       │   ├── cloud_sql_probe.py
│       │   └── circuit_breaker.py
│       │
│       └── clients/
│           ├── __init__.py
│           ├── identity.py
│           └── pubsub.py
│
├── tests/
│   ├── unit/
│   │   ├── test_normalize.py
│   │   ├── test_mapping.py
│   │   ├── test_validate_pre.py
│   │   └── test_validate_post.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_pipeline_happy.py
│   │   ├── test_pipeline_quarantine.py
│   │   ├── test_circuit_breaker.py
│   │   └── test_rls_isolation.py
│   └── fixtures/
│       ├── chunks/
│       └── mappings/
│
├── scripts/
│   ├── run-local.sh
│   └── replay-chunk.py
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### 3.2 Top-level items in this service

**`CLAUDE.md` (service-specific Claude Code instructions).**
Tells Claude Code: this service consumes from `ingress.ready`, transforms via the mapping engine, writes to canonical Postgres (with RLS) and emits audit. It is the *only* place where mapping is applied. PII tokenization has already happened at the receiver; this service should never see raw PII. The validation library is Pandera, not Great Expectations. The DB sink is RLS-aware; every write opens a transaction and sets `SET LOCAL app.tenant_id`. Why this file. Without it, Claude rediscovers these invariants every session, sometimes incorrectly. With it, Claude is reliably on-rails.

**`README.md`.**
Human-readable overview: what this service does, how to run locally, how to deploy, how to debug a stuck consumer, common failure modes and their signatures. Not a substitute for the architecture doc; a service-specific quick-reference.

**`pyproject.toml`.**
Service-specific Python dependencies (Pub/Sub client, Pandera, polars, psycopg, etc.). Pins workspace libraries (`dis-core`, `dis-canonical`, `dis-mapping`, `dis-validation`, `dis-rls`, `dis-audit`) by relative path so changes to those libs are picked up immediately. Why per-service. Each service has its own dependency surface; centralizing in one root `pyproject.toml` would force every service to install every other service's deps.

**`Dockerfile` and `.dockerignore`.**
Container build definition. Why per-service. Each service has different runtime dependencies (some need DuckDB, some don't; some need a GCP credential helper, some don't). A monorepo Dockerfile would either be bloated or have N variants; per-service is cleaner.

### 3.3 `src/streaming_consumer/` — the package

**Why `src/<package>/` layout, not just `src/`.**
The package is importable as `streaming_consumer`, not via relative imports. `src/` layout (vs. flat package at repo root) is the modern Python standard: ensures tests run against the installed package, not the source tree, which catches import errors and packaging bugs early.

**`main.py`.**
Entrypoint. Boots the Pub/Sub subscriber, wires the pipeline, runs the consumer loop. Stays thin: most logic lives in the modules below. Why thin. Easier to test the components in isolation; easier to swap the entrypoint when migrating to a higher-throughput runtime without rewriting the pipeline.

**`config.py`.**
Environment-driven configuration: Pub/Sub subscription name, Cloud SQL connection string, identity service URL, batch size, timeouts. Loaded from env vars, validated with Pydantic. Why centralized. Configuration scattered through the code is a known source of production surprises ("which env var does this read?"). One file, one model, one source of truth.

### 3.4 `src/streaming_consumer/pipeline/` — the transform graph

This is where the architecture's step-by-step ELT flow lives. One file per stage, matching the architecture document's numbered steps. The 1:1 correspondence between architecture steps and pipeline files is deliberate: when the doc says "step 6 normalization," there is exactly one file to open. Claude Code does noticeably better with this 1:1 mapping than with stages bundled into a single file.

**`fetch.py`** (architecture step 2).
Fetches the chunk from bronze metadata + GCS (or from Bronze PG payload depending on size). Returns a polars DataFrame plus chunk metadata. Why a separate file. Fetch is I/O-heavy; isolating it makes the rest of the pipeline pure-functional and easy to test.

**`mapping.py`** (architecture step 3).
Looks up the `(tenant_id, source_id)` mapping from the refreshing side-input cache. Returns the mapping spec (field_map, transforms, suite refs, mapping_version_id). Why separate. The side-input refresh logic (Pub/Sub `mapping.changed` listener + 30s fallback poll) lives here, isolated from the transform code.

**`normalize.py`** (architecture step 6, declarative + escape hatch).
The four sub-stages: rename, normalize, cast, derive. Holds the declarative transform vocabulary (`parse_date`, `parse_decimal`, etc.) and dispatches to named custom-transform functions via the escape hatch. Why one file for all four sub-stages. They share the same row-by-row dispatcher and the same error-emission path; splitting them would force three more files to coordinate. One file with clear sections is more readable.

**`validate_pre.py`** (architecture step 5, source-shape validation).
Loads the pre-mapping Pandera schema for the `(tenant, source, version)` and validates the raw chunk. Failure routes to chunk-level quarantine. Why a separate file from `validate_post`. Different vocabularies (source schema vs canonical schema), different failure granularities (chunk vs row), different schema sources. Conflating them is a footgun.

**`validate_post.py`** (architecture step 7, canonical-shape validation).
Loads the post-mapping Pandera schema and validates each canonical row candidate. Failure routes to per-row quarantine. Includes the FK pre-check against `identity_mirror` (avoids wasted DB round-trip for FK failures).

**`branch.py`** (architecture step 8).
The fork: valid rows go to canonical sinks; invalid rows go to quarantine. Pure function over the validation result. Why a separate file. Routing logic is small but architecturally load-bearing; isolating it makes the branching contract explicit and testable.

### 3.5 `src/streaming_consumer/sinks/` — output adapters

**Why `sinks/` separate from `pipeline/`.**
Pipeline stages transform data; sinks emit it. Different testing patterns (sinks need DB or Pub/Sub fakes; pipeline stages are pure functions). Different failure modes (sinks fail on infrastructure; pipeline stages fail on data). Splitting clarifies both.

**`canonical.py`.**
The RLS-aware transaction. Batches valid rows by `tenant_id`, opens a tx, runs `SET LOCAL app.tenant_id`, executes the hot-table merge upsert (event-time conditional) and history append, commits. Includes the FK retry-with-backoff loop for cases where `identity_mirror` is lagging. The single most safety-critical file in the service; documented heavily.

**`quarantine.py`.**
Publishes failed rows/chunks to the `quarantine` Pub/Sub topic. Includes the suite-failure context (which suite, which expectation, what value). Light-weight; mostly serialization.

**`dlq.py`.**
The Cloud SQL circuit-breaker fallback. When Cloud SQL is unhealthy, the canonical sink fails over to publishing batches to `pipeline.dlq`. The drainer (or a recovery job) replays from DLQ when health restores. Why separate from `canonical.py`. DLQ is an exceptional path; isolating it keeps the canonical sink's happy-path readable.

**`audit.py`.**
Emits structured audit events for every stage of every row via `libs/dis-audit`. Phase 1 destination is Cloud SQL `audit.events`; Phase 3 adds the BigQuery archive (see `decisions.md` D34). Why a sink (not a pipeline stage). Audit fires for both valid and invalid paths; it is not gated by the branch. Treating it as a sink (emit-on-event) rather than a stage (in the transform chain) is correct.

### 3.6 `src/streaming_consumer/health/` — circuit breaker and probes

**Why a separate folder.**
Health-check infrastructure (probes, circuit breakers, backpressure logic) is conceptually different from pipeline code. It's the "platform" code that protects the service from upstream/downstream failures. Splitting it makes the pipeline code purely about transformations.

**`cloud_sql_probe.py`.**
A `SELECT 1` probe with a 100ms timeout, invoked before each batch commit (architecture B3 / G3). Used by `circuit_breaker.py` to decide whether to commit to Cloud SQL or route to `pipeline.dlq`.

**`circuit_breaker.py`.**
State machine: closed (healthy), open (unhealthy, divert to DLQ), half-open (probing). Configurable thresholds; emits metrics on every state change for alerting.

### 3.7 `src/streaming_consumer/clients/` — external service wrappers

**Why `clients/` separate from `sinks/`.**
Sinks are where data lands; clients are how this service *calls* other services. Different semantics: clients are request/response, sinks are append-only emissions. Different testing: clients need response mocking and error injection; sinks need real or faked DB/Pub/Sub.

**`identity.py`.**
Wraps the Identity Service client (`validate(tenant_id, store_id)` for the FK pre-check). Includes the stale-while-error fallback to `identity_mirror` direct-read when the Identity Service circuit is open.

**`pubsub.py`.**
Thin wrapper over the Pub/Sub subscriber client, with the streaming pull semantics, ack/nack handling, and the ordering-key contract (tenant_id as the ordering key per architecture). Centralized so retry, ack-deadline extension, and metrics are consistent.

### 3.8 `tests/` — service tests

**Why the split into `unit/`, `integration/`, `fixtures/`.**
Different gates in CI. Unit tests run on every push (pure functions, milliseconds per test). Integration tests run on PR (mocked externals, single-digit seconds per test). Fixtures are reusable test data, shared across both. Conflating them runs slow tests too often and fast tests too rarely.

**`tests/unit/`.**
Pure-function tests with no I/O. `test_normalize.py` exercises the normalize engine against handcrafted inputs. `test_mapping.py` tests mapping lookup with an in-memory config. `test_validate_pre.py` and `test_validate_post.py` exercise Pandera schemas with crafted DataFrames. Why pure. Tests should fail because logic is wrong, not because Docker isn't running.

**`tests/integration/`.**
Multi-component tests with mocked externals. `conftest.py` provides shared pytest fixtures: a mock Identity Service, an in-memory Pub/Sub fake, a transient Cloud SQL instance (testcontainers). `test_pipeline_happy.py` runs a full chunk through fetch→mapping→normalize→validate→sink and asserts canonical rows land correctly with RLS scoping. `test_pipeline_quarantine.py` runs malformed chunks and asserts they land in quarantine with the right failure reason. `test_circuit_breaker.py` simulates Cloud SQL unhealth and asserts DLQ routing. `test_rls_isolation.py` writes from two tenants and asserts cross-tenant reads are blocked.

**`tests/fixtures/`.**
Real sample CSVs, mapping configs, expected canonical outputs. These are the *contract* between the test suite and the service's behavior; treating them as first-class artifacts (not inline test data) makes them reviewable and reusable. `chunks/` holds sample ingress chunks (CSVs, JSON payloads). `mappings/` holds per-(tenant, source) mapping configs paired with chunks.

### 3.9 `scripts/` — service-local dev tooling

**Why scripts inside the service.**
Service-specific operational helpers belong with the service, not in the repo-root `tools/`. `tools/` is for cross-cutting platform tools.

**`run-local.sh`.**
Boots the service against the dev docker-compose stack (Pub/Sub emulator, Postgres, fake Identity Service). One command from clone to running consumer. Why this matters. The difference between a tight Claude Code iteration loop and a slow one is whether the service can be exercised end-to-end in seconds. `run-local.sh` is the difference.

**`replay-chunk.py`.**
Re-publishes a single bronze chunk to `ingress.ready` for debugging. Useful for "this chunk failed; let me re-run it locally with a debugger attached."

### 3.10 `deploy/` — service-specific deployment manifests

**Why per-service deploy/, not all in root `infra/`.**
Service-specific manifests (the Deployment, ConfigMap, Service) live with the service because they evolve with the service code. Cross-cutting infra (Cloud SQL instances, Pub/Sub topics, IAM bindings) lives in root `infra/` because those are platform-level and shared.

**`service.yaml`.**
The Kubernetes Deployment + Service (). Pinned to the image built from this directory's Dockerfile.

**`configmap.yaml`.**
Non-secret configuration: subscription names, batch sizes, timeouts. Secrets are handled separately via the cluster's secret manager; not committed.

**`deploy/README.md`.**
How to deploy this service: prerequisites, command, rollback procedure, what to watch on the dashboard after deploy. Short, operational, current.

### 3.11 What is deliberately NOT in this service

- **No `models/` directory.** Pydantic models for canonical, mapping, audit, etc. live in shared libraries (`libs/dis-canonical`, `libs/dis-mapping`, `libs/dis-audit`). The service imports them. Why. Models are contracts; contracts belong with the schema, not duplicated per service.

- **No `utils/`.** Tempting and always wrong. Helpers belong in the module that uses them, or in a focused shared library, not in a junk drawer.

- **No `migrations/`.** Schema migrations live in `schemas/` (the repo-root dbt project), not per-service. Services don't own schema; they consume it.

- **No service-level `docs/`.** The service README plus its CLAUDE.md is the operational documentation. Architecture-level docs live in repo-root `docs/`.

- **No `experiments/` or `playground/`.** The whole codebase is greenfield experimentation. A separate folder for that would either go stale or become the place where real code accumulates without review.

---
