# DIS Repository Structure

**Audience:** anyone navigating the DIS monorepo. Carries the directory tree for every service, lib, and top-level directory.
**Scope:** file layout only. Single-line descriptions inline. For behavioural detail (EPE — Entry / Process / Exit — blocks), interface contracts, or rules, see the `README.md` and `CLAUDE.md` files in each directory.

**Companion docs.**
- `architecture.md` — system rationale.
- `decisions.md` — indexed decision register.
- `engineering-reference.md` — top-level index pointing into per-directory files.
- `build-guide.md` — build phases, target portability, worked examples.
- `cost-estimate.md` — cost projection.

---

## 1. Top-level layout

```
ithina-dis/
├── README.md                # project overview, getting started
├── CLAUDE.md                # project-wide Claude Code instructions (auto-loaded)
├── architecture.md          # current architecture doc (or symlink to docs/)
├── pyproject.toml           # uv workspace root; enumerates services and libs
├── uv.lock                  # locked Python dependency graph
├── Makefile                 # common dev commands (make test/lint/build)
│
├── docs/                    # architecture docs, ADRs, runbooks, slices, API specs
├── services/                # containerised services (twelve, one per dir; see §2)
├── libs/                    # shared Python libraries (nine; see §3)
├── ui/                      # DIS UI (TypeScript/React, single container)
├── schemas/                 # SQL DDL + dbt models for Cloud SQL and BQ
├── contracts/               # Pub/Sub Avro/Proto, gRPC, Customer Master JWT contract
├── infra/                   # Terraform, k8s manifests, per-env config
├── tools/                   # operator tooling (replay, load test, codegen)
├── alembic/                 # Postgres migrations (canonical, config, bronze, identity_mirror, quarantine, staging, audit)
├── dbt/                     # BigQuery dbt project (canonical_history.* models)
└── tests/                   # cross-cutting tests (integration, e2e, contract)
```

---

## 2. Services

One subdirectory per deployable service. Twelve in total; seven ship in v1.0 (six backend services plus `dis-ui` frontend), the others are designed-for but deferred.

### services/receiver-api/

```
services/receiver-api/            # API/webhook ingress receiver (deferred, not v1.0)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── receiver_api/
│       ├── __init__.py
│       ├── main.py             # HTTP server entrypoint (FastAPI / Litestar)
│       ├── config.py
│       │
│       ├── handlers/           # one HTTP handler per endpoint
│       │   ├── __init__.py
│       │   ├── ingest.py       # POST /ingest (API push)
│       │   └── webhook.py      # POST /webhook/{partner_id}
│       │
│       ├── enrichment/         # attach identity + trace_id to incoming chunk
│       │   ├── __init__.py
│       │   ├── identity.py     # call Identity Service resolve_from_token
│       │   ├── trace.py        # trace_id generation and propagation
│       │   └── pii.py          # PII tokenization (HMAC, per-tenant key)
│       │
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── gcs.py          # write raw payload to bronze GCS
│       │   ├── bronze.py       # write metadata row to bronze Postgres
│       │   └── pubsub.py       # publish ingress.ready message
│       │
│       └── clients/
│           ├── __init__.py
│           ├── identity.py     # Identity Service client
│           └── customer_master.py  # token validation (where machine auth applies)
│
├── tests/
│   ├── unit/
│   │   ├── test_enrichment.py
│   │   ├── test_pii_tokenization.py
│   │   └── test_handlers.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_happy_path.py
│   │   ├── test_auth_failures.py
│   │   └── test_idempotency.py
│   └── fixtures/
│       └── payloads/
│
├── scripts/
│   ├── run-local.sh
│   └── replay-request.sh       # curl-shaped local request helper
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### services/csv-ingest-worker/

The Phase 1 signed-URL endpoint lives in `services/dis-ui-server/` as a handler (per `decisions.md` D36), not here. This service is the GCS-event-triggered worker that runs the heavy work after the upload lands in GCS.

```
services/csv-ingest-worker/       # GCS-event-triggered CSV ingest worker (Phase 2 of CSV upload; v1.0)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── csv_ingest_worker/
│       ├── __init__.py
│       ├── main.py             # Pub/Sub subscriber entrypoint
│       ├── config.py
│       │
│       ├── notifications/      # GCS object-finalized event handler
│       │   ├── __init__.py
│       │   └── handler.py      # subscribed to bucket.objects.changed Pub/Sub
│       │
│       ├── enrichment/         # runs on notification, before bronze write
│       │   ├── __init__.py
│       │   ├── identity.py     # call Identity Service resolve_from_upload
│       │   ├── trace.py        # read trace_id from GCS path; this service does NOT mint trace_ids
│       │   └── pii.py          # tokenize PII before any persisted reference
│       │
│       ├── preflight/          # DuckDB-driven CSV pre-flight after upload completes
│       │   ├── __init__.py
│       │   ├── duckdb_check.py # row count, columns, null %, type sniff
│       │   └── rules.py        # baseline checks (size, MIME, header present)
│       │
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── bronze.py       # write metadata row
│       │   ├── pubsub.py       # publish ingress.ready
│       │   └── quarantine.py   # publish to quarantine topic on preflight failure
│       │
│       └── clients/
│           ├── __init__.py
│           └── identity.py
│
├── tests/
│   ├── unit/
│   │   ├── test_preflight.py
│   │   ├── test_enrichment.py
│   │   ├── test_idempotency.py
│   │   └── test_notification_handler.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_object_finalized_flow.py
│   │   ├── test_csv_malformed.py
│   │   ├── test_csv_too_large.py
│   │   └── test_csv_empty.py
│   └── fixtures/
│       └── csvs/               # sample CSVs (good, malformed, edge cases)
│
├── scripts/
│   └── run-local.sh
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### services/receiver-csv-erp/

```
services/receiver-csv-erp/        # per-tenant ERP CSV POST endpoint (deferred)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── receiver_csv_erp/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       │
│       ├── handlers/
│       │   ├── __init__.py
│       │   └── erp_post.py     # POST /tenant/{tenant_id}/erp: issues signed URL
│       │
│       ├── notifications/      # GCS object-finalized event handler
│       │   ├── __init__.py
│       │   └── handler.py
│       │
│       ├── auth/               # ERP-specific auth (machine credentials)
│       │   ├── __init__.py
│       │   ├── api_key.py      # per-tenant API key validation
│       │   └── mtls.py         # mTLS cert validation
│       │
│       ├── enrichment/
│       │   ├── __init__.py
│       │   ├── identity.py     # call Identity Service resolve_from_endpoint
│       │   ├── trace.py
│       │   └── pii.py
│       │
│       ├── preflight/
│       │   ├── __init__.py
│       │   ├── duckdb_check.py
│       │   └── rules.py
│       │
│       ├── ratelimit/          # per-tenant rate limit (architecture B3 fix #1)
│       │   ├── __init__.py
│       │   └── token_bucket.py
│       │
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── bronze.py       # write metadata row (post-notification)
│       │   └── pubsub.py
│       │
│       └── clients/
│           ├── __init__.py
│           └── identity.py
│
├── tests/
│   ├── unit/
│   │   ├── test_auth_api_key.py
│   │   ├── test_auth_mtls.py
│   │   ├── test_ratelimit.py
│   │   ├── test_preflight.py
│   │   └── test_notification_handler.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_erp_signed_url.py
│   │   ├── test_object_finalized_flow.py
│   │   ├── test_erp_throttled.py
│   │   └── test_erp_auth_failure.py
│   └── fixtures/
│       └── csvs/
│
├── scripts/
│   ├── run-local.sh
│   └── post-local.sh
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### services/receiver-reverse-api/

```
services/receiver-reverse-api/    # reverse-API puller, cursor-based (deferred)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── receiver_reverse_api/
│       ├── __init__.py
│       ├── main.py             # scheduler entrypoint, not HTTP server
│       ├── config.py
│       │
│       ├── puller/             # per-pull-target logic
│       │   ├── __init__.py
│       │   ├── scheduler.py    # which targets to pull, when
│       │   ├── http_puller.py  # generic HTTP GET + auth
│       │   └── paginator.py    # cursor/offset/page pagination strategies
│       │
│       ├── enrichment/
│       │   ├── __init__.py
│       │   ├── identity.py     # call Identity Service resolve_from_endpoint
│       │   ├── trace.py
│       │   └── pii.py
│       │
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── gcs.py
│       │   ├── bronze.py
│       │   └── pubsub.py
│       │
│       ├── state/              # pull state per target (last cursor, last ts)
│       │   ├── __init__.py
│       │   └── cursor_store.py
│       │
│       └── clients/
│           ├── __init__.py
│           └── identity.py
│
├── tests/
│   ├── unit/
│   │   ├── test_paginator.py
│   │   ├── test_cursor_store.py
│   │   └── test_scheduler.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_pull_happy.py
│   │   ├── test_pull_paginated.py
│   │   └── test_pull_resume.py
│   └── fixtures/
│       └── responses/          # mock external API responses
│
├── scripts/
│   ├── run-local.sh
│   └── pull-once.sh            # one-shot manual trigger of a pull
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### services/identity-service/

```
services/identity-service/        # tenant/store resolution; mediates Customer Master (v1.0)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── identity_service/
│       ├── __init__.py
│       ├── main.py             # gRPC + REST server entrypoint
│       ├── config.py
│       │
│       ├── handlers/           # interface methods (per architecture §4.2)
│       │   ├── __init__.py
│       │   ├── resolve_from_token.py
│       │   ├── resolve_from_upload.py
│       │   ├── resolve_from_endpoint.py
│       │   └── validate.py
│       │
│       ├── cache/
│       │   ├── __init__.py
│       │   ├── store.py        # Redis or in-process LRU
│       │   ├── ttl.py          # TTL policy (5-15 min)
│       │   └── invalidator.py  # subscribe to identity.changed for cache evict
│       │
│       ├── admin_db/           # the only place admin DB credentials live
│       │   ├── __init__.py
│       │   ├── client.py
│       │   └── queries.py
│       │
│       ├── publisher/          # publish identity.changed on admin DB writes
│       │   ├── __init__.py
│       │   └── changed_events.py
│       │
│       └── health/             # stale-while-error logic
│           ├── __init__.py
│           └── circuit_breaker.py
│
├── tests/
│   ├── unit/
│   │   ├── test_cache.py
│   │   ├── test_ttl.py
│   │   └── test_resolve_handlers.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_resolve_cached.py
│   │   ├── test_resolve_miss.py
│   │   ├── test_stale_while_error.py
│   │   └── test_changed_event_publish.py
│   └── fixtures/
│       └── admin_db_seed.sql
│
├── scripts/
│   ├── run-local.sh
│   └── seed-admin-db.sh
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### services/mirror-sync-consumer/

```
services/mirror-sync-consumer/    # maintains identity_mirror schema (v1.0)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── mirror_sync_consumer/
│       ├── __init__.py
│       ├── main.py             # entrypoint (dispatches to consumer or pull mode)
│       ├── config.py
│       │
│       ├── consumer/           # Pub/Sub mode (deferred until CM emits)
│       │   ├── __init__.py
│       │   ├── subscribe.py    # Pub/Sub pull loop
│       │   └── handler.py      # dispatch by event type
│       │
│       ├── pull/               # DB-pull mode (v1.0 launch path)
│       │   ├── __init__.py
│       │   ├── runner.py       # CLI / scheduler entrypoint
│       │   ├── reader.py       # reads CM Postgres tenants + stores
│       │   └── reconcile.py    # flags drift between mirror and source
│       │
│       ├── sync/               # shared upsert logic (used by both modes)
│       │   ├── __init__.py
│       │   ├── tenants.py      # upsert tenants
│       │   └── stores.py       # upsert stores (soft-delete via is_active)
│       │
│       └── sinks/
│           ├── __init__.py
│           └── postgres.py     # writes to identity_mirror schema
│
├── tests/
│   ├── unit/
│   │   ├── test_handler_dispatch.py
│   │   ├── test_tenants_sync.py
│   │   └── test_stores_sync.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_create_event.py
│   │   ├── test_update_event.py
│   │   └── test_soft_delete_event.py
│   └── fixtures/
│       └── events/             # sample identity.changed events
│
├── scripts/
│   ├── run-local.sh
│   └── replay-events.sh        # replay events from a saved snapshot
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### services/streaming-consumer/

```
services/streaming-consumer/      # the ELT pipeline; atomic dual-write to canonical hot + event tables (v1.0)
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

### services/quarantine-drainer/

```
services/quarantine-drainer/      # quarantine Pub/Sub -> Cloud SQL drainer (v1.0)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── quarantine_drainer/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       │
│       ├── consumer/
│       │   ├── __init__.py
│       │   ├── subscribe.py
│       │   └── handler.py      # dispatch by failure type
│       │
│       ├── enrichment/         # add context the streaming consumer didn't include
│       │   ├── __init__.py
│       │   ├── lineage.py      # parent_trace_id resolution
│       │   └── suite_link.py   # link to suite failure docs (Pandera output)
│       │
│       └── sinks/
│           ├── __init__.py
│           └── postgres.py     # write to quarantine.* tables (RLS)
│
├── tests/
│   ├── unit/
│   │   ├── test_handler_dispatch.py
│   │   ├── test_lineage.py
│   │   └── test_postgres_sink.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_source_shape_failure.py
│   │   ├── test_normalization_failure.py
│   │   ├── test_canonical_shape_failure.py
│   │   └── test_idempotency.py
│   └── fixtures/
│       └── failures/           # sample quarantine messages by type
│
├── scripts/
│   ├── run-local.sh
│   └── replay-quarantine.sh
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### services/nightly-batch/

```
services/nightly-batch/           # daily BQ export + retention eviction (v1.0)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── nightly_batch/
│       ├── __init__.py
│       ├── main.py             # job entrypoint (one run = one full cycle)
│       ├── config.py
│       │
│       ├── steps/              # ordered batch steps
│       │   ├── __init__.py
│       │   ├── 01_watermark.py     # determine yesterday's slice
│       │   ├── 02_quality_gate.py  # optional Pandera suite on the slice
│       │   ├── 03_load_to_bq.py    # Storage Write API into canonical_history
│       │   ├── 04_verify_bq.py     # row count + checksum verify
│       │   └── 05_evict_sql.py     # delete > 35d from Cloud SQL (batched; window configurable per D29)
│       │
│       ├── idempotency/        # safe re-runs
│       │   ├── __init__.py
│       │   └── job_state.py    # tracks which steps completed for a given run
│       │
│       └── clients/
│           ├── __init__.py
│           ├── bigquery.py
│           └── postgres.py
│
├── tests/
│   ├── unit/
│   │   ├── test_watermark.py
│   │   ├── test_quality_gate.py
│   │   └── test_evict_batching.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_full_cycle.py
│   │   ├── test_resume_after_failure.py
│   │   └── test_idempotent_rerun.py
│   └── fixtures/
│       └── history_slices/     # synthetic history data for testing
│
├── scripts/
│   ├── run-local.sh
│   └── run-once.sh             # manual one-shot trigger
│
└── deploy/
    ├── cronjob.yaml            # k8s CronJob or Cloud Scheduler trigger
    ├── configmap.yaml
    └── README.md
```

### services/dis-ui/

The DIS frontend application. Single containerized service hosting the user-facing UI (tenant + ops surfaces). Auth via Customer Master. Calls one backend: `dis-ui-server`.

**Status:** scaffolding placeholder (v1.0). Stack, build tooling, and folder layout chosen in build-guide Slice 19 (DIS UI foundation). Until that slice runs, this directory only reserves the service slot.

```
services/dis-ui/                  # DIS frontend application (v1.0; structure decided in Slice 19)
├── CLAUDE.md
└── README.md
```

### services/dis-ui-server/

```
services/dis-ui-server/                 # BFF for DIS UI; hosts onboarding sub-module in-process (v1.0)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── dis_ui_server/
│       ├── __init__.py
│       ├── main.py             # HTTP server entrypoint (FastAPI)
│       ├── config.py
│       │
│       ├── auth/               # Customer Master token validation
│       │   ├── __init__.py
│       │   ├── verifier.py     # JWT signature + claims validation
│       │   └── scope.py        # tenant_id extraction + RBAC enforcement
│       │
│       ├── handlers/           # one per DIS UI sub-module (FastAPI routers)
│       │   ├── __init__.py
│       │   ├── upload_session.py  # Phase 1 of CSV upload: signed PUT URL issuance (D36)
│       │   ├── sample_upload.py
│       │   ├── onboarding_review.py
│       │   ├── mapping_crud.py
│       │   ├── quarantine.py
│       │   ├── audit.py
│       │   └── duckdb_query.py
│       │
│       ├── onboarding/         # merged from former onboarding-service
│       │   ├── __init__.py
│       │   ├── inference/      # Layer 1: schema inference (DuckDB-driven)
│       │   │   ├── __init__.py
│       │   │   ├── duckdb_describe.py
│       │   │   ├── type_sniff.py
│       │   │   └── null_profile.py
│       │   ├── suggestion/     # Layer 2: mapping + normalization suggestion
│       │   │   ├── __init__.py
│       │   │   ├── name_match.py
│       │   │   ├── value_match.py
│       │   │   ├── historical.py
│       │   │   └── normalize_rules.py
│       │   ├── validation_draft/   # Layer 3: validation suite proposals
│       │   │   ├── __init__.py
│       │   │   ├── source_shape.py
│       │   │   └── canonical_shape.py
│       │   └── shadow/         # shadow rollout coordination
│       │       ├── __init__.py
│       │       └── compare.py
│       │
│       ├── repos/              # read-side data access (one per data source)
│       │   ├── __init__.py
│       │   ├── canonical_replica.py    # reads from Cloud SQL read replica
│       │   ├── config.py               # config.source_mappings CRUD
│       │   ├── quarantine.py           # quarantine.* table queries
│       │   └── audit.py               # audit.events queries (Cloud SQL in Phase 1; BQ-augmented in Phase 3)
│       │
│       ├── duckdb_runner/      # ad-hoc query panel (ops-restricted)
│       │   ├── __init__.py
│       │   └── runner.py
│       │
│       └── clients/
│           ├── __init__.py
│           └── customer_master.py
│
├── tests/
│   ├── unit/
│   │   ├── test_auth.py
│   │   ├── test_scope.py
│   │   ├── test_handlers.py
│   │   ├── test_repos.py
│   │   ├── test_inference.py
│   │   ├── test_name_match.py
│   │   ├── test_normalize_rules.py
│   │   └── test_validation_draft.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_mapping_crud.py
│   │   ├── test_quarantine_views.py
│   │   ├── test_audit_lookup.py
│   │   ├── test_duckdb_panel.py
│   │   ├── test_tenant_scope_enforcement.py
│   │   ├── test_onboarding_full_pipeline.py
│   │   ├── test_promote_staged.py
│   │   └── test_shadow_compare.py
│   └── fixtures/
│       ├── tokens/             # signed Customer Master tokens for tests
│       ├── canonical_seeds/
│       ├── samples/            # sample CSVs from known source types
│       └── golden_mappings/    # expected mapping outputs for review
│
├── scripts/
│   ├── run-local.sh
│   ├── curl-handler.sh
│   └── analyze-sample.py       # one-shot CLI analyzer for debugging
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

### services/daily-compute/

```
services/daily-compute/           # incremental signal compute (v1.0)
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── daily_compute/
│       ├── __init__.py
│       ├── main.py             # job entrypoint (one run = one full cycle for one date)
│       ├── config.py
│       │
│       ├── compute/            # per-signal compute logic
│       │   ├── __init__.py
│       │   ├── velocity.py     # velocity_7day
│       │   ├── stock_age.py    # stock_age_days
│       │   └── cost_trend.py   # unit_cost_trend_30day
│       │
│       ├── readers/
│       │   ├── __init__.py
│       │   ├── yesterday_signals.py  # read store_sku_signal_history
│       │   ├── today_events.py       # read sale + change events
│       │   └── bq_fallback.py        # slow path for missing yesterday
│       │
│       ├── writers/
│       │   ├── __init__.py
│       │   ├── signal_history.py     # INSERT into store_sku_signal_history
│       │   └── current_position.py   # UPDATE store_sku_current_position
│       │
│       ├── orchestration/
│       │   ├── __init__.py
│       │   ├── per_tenant.py         # iterate tenants with RLS context
│       │   └── per_sku.py            # iterate SKUs within a tenant
│       │
│       ├── idempotency/
│       │   ├── __init__.py
│       │   └── job_state.py          # date-watermark persistence
│       │
│       └── audit.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── scripts/
└── deploy/
```

---

## 3. Libraries

Shared Python libraries used by multiple services. Each library has a focused purpose.

### libs/dis-core/

```
libs/dis-core/            # logging, IDs (UUIDv7), BqClient wrapper, audit helpers, errors
├── pyproject.toml
├── README.md
├── src/
│   └── dis_core/
│       ├── __init__.py
│       ├── ids.py              # trace_id, tenant_id, store_id types
│       ├── timestamps.py       # event_ts, received_ts handling
│       ├── errors.py           # exception hierarchy
│       ├── result.py           # Result type for fallible operations
│       └── logging.py          # structured logging conventions
└── tests/
    └── unit/
```

### libs/dis-canonical/

```
libs/dis-canonical/       # canonical schema models (Pydantic)
├── pyproject.toml
├── README.md
├── src/
│   └── dis_canonical/
│       ├── __init__.py
│       ├── hot/
│       │   ├── __init__.py
│       │   └── store_sku_current_position.py
│       ├── history/
│       │   ├── __init__.py
│       │   └── events.py
│       └── shared/
│           ├── __init__.py
│           └── identifiers.py  # sku_id, store_id formats
└── tests/
    └── unit/
```

### libs/dis-mapping/

```
libs/dis-mapping/         # pure mapping engine: rename / normalize / cast / derive (slice-05)
├── pyproject.toml
├── README.md
├── src/
│   └── dis_mapping/
│       ├── __init__.py
│       ├── result.py               # MappingResult + CellNormalizationFailure (partial contribution)
│       ├── models/
│       │   ├── __init__.py
│       │   ├── source_mapping.py   # the mapping_rules contract (D49): ordered transform lists
│       │   └── transform.py        # bounded vocabulary + per-op arg validation
│       └── engine/
│           ├── __init__.py
│           ├── apply.py            # apply_mapping composition
│           ├── rename.py
│           ├── normalize.py        # op impls; ordered per-column lists
│           ├── cast.py
│           └── derive.py
└── tests/
    └── unit/
```

Note: the formerly-reserved `escape_hatch/` registry is DEFERRED and does not exist
(slice-05 scope boundary; the bounded declarative vocabulary is the only transform surface).

### libs/dis-validation/

```
libs/dis-validation/      # Pandera suites; pre-mapping and post-mapping (slice-05)
├── pyproject.toml
├── README.md
├── src/
│   └── dis_validation/
│       ├── __init__.py
│       ├── provenance.py       # mapping-produced vs consumer-injected line + drift guard
│       ├── source_shape.py     # SourceShapeSuiteDef (+ from_rename) + materializer
│       ├── canonical_shape.py  # CanonicalShapeSuiteDef + model-derived materializer
│       ├── runner.py           # run_source_shape / run_canonical_shape (D50 pre-check)
│       └── failure_formatter.py # tenant-readable failure reasons
└── tests/
    └── unit/
```

Note: the formerly-reserved `suite_loader.py` (a DB-backed `(tenant, source, version)`
loader) is the CONSUMER's side-input (Slice 10), not this pure lib's — this lib
materializes handed-in definitions only.

### libs/dis-rls/

```
libs/dis-rls/             # RLS-aware DB session helpers
├── pyproject.toml
├── README.md
├── src/
│   └── dis_rls/
│       ├── __init__.py
│       ├── session.py          # context manager: open tx, set tenant, run, commit
│       ├── batch.py            # batched-by-tenant transaction wrapper
│       └── enforcement.py      # assertions: this connection has tenant set
└── tests/
    └── unit/
```

### libs/dis-audit/

```
libs/dis-audit/           # audit event emission
├── pyproject.toml
├── README.md
├── src/
│   └── dis_audit/
│       ├── __init__.py
│       ├── event.py            # AuditEvent Pydantic model
│       ├── writer.py           # backend-selecting writer (Phase 1: postgres; Phase 3: + bigquery)
│       ├── postgres_writer.py  # writes to Cloud SQL audit.events (Phase 1 active)
│       ├── bigquery_writer.py  # writes to BigQuery audit_events (Phase 3 stub)
│       └── stages.py           # enum of pipeline stages
└── tests/
    └── unit/
```

### libs/dis-pii/

```
libs/dis-pii/             # PII tokenization (deterministic HMAC, per-tenant keys)
├── pyproject.toml
├── README.md
├── src/
│   └── dis_pii/
│       ├── __init__.py
│       ├── tokenizer.py        # HMAC token generation
│       ├── key_vault.py        # per-tenant key lookup + rotation
│       ├── detectors.py        # field-name and pattern-based PII detection
│       └── policy.py           # what to tokenize per source type
└── tests/
    └── unit/
```

### libs/dis-storage/

```
libs/dis-storage/         # GCS access; canonical path scheme; signed URLs
├── pyproject.toml
├── README.md
├── src/
│   └── dis_storage/
│       ├── __init__.py
│       ├── paths.py            # build_object_path(tenant_id, source_id, trace_id, event_ts)
│       ├── metadata.py         # build_object_metadata(...), assert_path_matches_metadata(...)
│       ├── signed_urls.py      # tenant-facing signed URLs for direct upload
│       ├── notifications.py    # parse GCS object-finalized notifications
│       └── client.py           # GCS client wrapper (honors STORAGE_EMULATOR_HOST)
└── tests/
    └── unit/
```

### libs/dis-testing/

```
libs/dis-testing/         # test fixtures, factories, RLS helpers
├── pyproject.toml
├── README.md
├── src/
│   └── dis_testing/
│       ├── __init__.py
│       ├── fakes/
│       │   ├── identity_service.py     # fake Identity Service for service tests
│       │   ├── pubsub.py               # in-memory Pub/Sub
│       │   └── customer_master.py      # fake CM token issuer
│       ├── factories/                  # build test objects
│       │   ├── canonical_rows.py
│       │   ├── mappings.py
│       │   └── audit_events.py
│       └── docker_compose/             # the shared dev stack definition
│           └── docker-compose.yml
└── tests/
```
