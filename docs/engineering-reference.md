# DIS Engineering Reference

**Audience:** Claude Code working in this repo, plus engineers orienting themselves.
**Purpose:** thin top-level index pointing into per-service and per-lib reference. Detail lives in `services/<name>/README.md` and `libs/<name>/README.md`, not here.

**Companion docs.**
- `architecture.md` — WHY (modules, data flow, isolation).
- `architecture.html` — visual diagram.
- `decisions.md` — indexed decision register (D1-Dn).
- `repo-structure.md` — detailed directory trees for every service, lib, and top-level directory.
- `build-guide.md` — HOW (build phases, target portability, worked examples).
- `cost-estimate.md` — cost projection.

---

## Repository layout

```
ithina-dis/
├── CLAUDE.md                    # Root project memory (auto-loaded by Claude Code)
├── README.md                    # Project overview
├── pyproject.toml               # Monorepo build config (uv workspace)
├── docker-compose.yml           # Local dev stack
├── .env.example                 # Required env vars
│
├── services/                    # Containerised services
│   ├── csv-ingest-worker/       # GCS-event-triggered CSV ingest worker (Phase 2; v1.0). Phase 1 lives in dis-ui-server per D36.
│   ├── receiver-api/            # API/webhook receiver (deferred)
│   ├── receiver-csv-erp/        # Per-tenant ERP CSV POST (deferred)
│   ├── receiver-reverse-api/    # Reverse-API puller (deferred)
│   ├── identity-service/        # Tenant/store resolution + validation (v1.0)
│   ├── mirror-sync-consumer/    # Maintains identity_mirror schema (v1.0)
│   ├── streaming-consumer/      # The ELT pipeline (v1.0)
│   ├── quarantine-drainer/      # Quarantine Pub/Sub → Cloud SQL drainer (v1.0)
│   ├── nightly-batch/           # Daily BQ export + retention eviction (v1.0)
│   ├── daily-compute/           # Postgres-local signal compute (v1.0)
│   ├── dis-ui/                  # DIS frontend application (v1.0; placeholder until Slice 19)
│   └── dis-ui-server/                 # BFF for DIS UI (v1.0)
│
├── libs/                        # Shared Python libraries
│   ├── dis-core/                # Logging, IDs, BqClient, audit helpers
│   ├── dis-canonical/           # Canonical schema models (Pydantic)
│   ├── dis-mapping/             # Mapping engine
│   ├── dis-validation/          # Pandera suites
│   ├── dis-rls/                 # RLS-aware DB session helpers
│   ├── dis-audit/               # Audit event emission
│   ├── dis-pii/                 # PII tokenization
│   ├── dis-storage/             # GCS access + canonical paths
│   └── dis-testing/             # Test fixtures and helpers
│
├── ui/                          # DIS UI (TypeScript + React)
├── schemas/                     # Pub/Sub Avro/Proto schemas
├── contracts/                   # External-system contracts (identity-service, Customer Master)
├── infra/                       # Terraform, Kubernetes manifests
├── tools/                       # Codegen, replay, load-test
├── alembic/                     # Postgres migrations (root-level)
├── dbt/                         # BigQuery dbt project
├── tests/                       # Cross-service integration tests
└── docs/                        # Architecture, decisions, slices
```

For detailed per-service and per-lib directory trees, see `repo-structure.md` (or the `README.md` inside each directory).

---

## Services (per-directory reference)

Each service has its own `README.md` (full EPE — Entry / Process / Exit — block, file structure, behavioural detail) and `CLAUDE.md` (service-specific rules, lazy-loaded by Claude Code).

| Service | Status | Purpose |
|---|---|---|
| [`csv-ingest-worker`](services/csv-ingest-worker/README.md) | v1.0 | GCS-event-triggered CSV ingest worker (Phase 2). Phase 1 upload-URL endpoint lives in dis-ui-server per `decisions.md` D36. |
| [`dis-ui`](services/dis-ui/README.md) | v1.0 | DIS frontend application. Placeholder until build-guide Slice 19 (DIS UI foundation). |
| [`receiver-api`](services/receiver-api/README.md) | deferred | API/webhook ingress receiver. |
| [`receiver-csv-erp`](services/receiver-csv-erp/README.md) | deferred | Per-tenant ERP CSV POST endpoint. |
| [`receiver-reverse-api`](services/receiver-reverse-api/README.md) | deferred | Reverse-API puller (cursor-based). |
| [`identity-service`](services/identity-service/README.md) | v1.0 | Tenant/store resolution; mediates Customer Master access. |
| [`mirror-sync-consumer`](services/mirror-sync-consumer/README.md) | v1.0 | Maintains `identity_mirror` schema. DB-pull from Customer Master DB in v1.0; Pub/Sub consumer post-v1.0. |
| [`streaming-consumer`](services/streaming-consumer/README.md) | v1.0 | The ELT pipeline. Atomic dual-write to canonical hot + event tables. |
| [`quarantine-drainer`](services/quarantine-drainer/README.md) | v1.0 | Writes quarantine events to Cloud SQL. |
| [`nightly-batch`](services/nightly-batch/README.md) | v1.0 | Daily BQ export + Cloud SQL partition eviction. |
| [`daily-compute`](services/daily-compute/README.md) | v1.0 | Incremental signal compute. |
| [`dis-ui-server`](services/dis-ui-server/README.md) | v1.0 | BFF for DIS UI. Hosts onboarding sub-module in-process. |

---

## Libraries (per-directory reference)

| Lib | Purpose |
|---|---|
| [`dis-core`](libs/dis-core/README.md) | Logging, IDs (UUIDv7), audit helpers, error types. BqClient stub in Phase 1 (real in Phase 3). |
| [`dis-canonical`](libs/dis-canonical/README.md) | Canonical schema models (Pydantic). |
| [`dis-mapping`](libs/dis-mapping/README.md) | Mapping engine. Four sub-stages: rename, normalize, cast, derive. |
| [`dis-validation`](libs/dis-validation/README.md) | Pandera suites. |
| [`dis-rls`](libs/dis-rls/README.md) | RLS-aware DB session helpers. |
| [`dis-audit`](libs/dis-audit/README.md) | Audit event emission. Writes to Cloud SQL `audit.events` in Phase 1; BigQuery archive in Phase 3. |
| [`dis-pii`](libs/dis-pii/README.md) | PII tokenization. |
| [`dis-storage`](libs/dis-storage/README.md) | GCS access; canonical path scheme. |
| [`dis-testing`](libs/dis-testing/README.md) | Test fixtures and helpers. |

---

## Schemas, contracts, infra, tools, tests

- **`schemas/`** — Pub/Sub Avro/Proto schemas (`ingress.ready`, `ingress.resubmit`, `identity.changed`, `quarantine`, `mapping.changed`, `pipeline.dlq`). Schema registry pattern.
- **`contracts/`** — External-system contract files (identity-service OpenAPI/proto, Customer Master JWT claims contract).
- **`infra/`** — Terraform for GCP, Kubernetes manifests, CI config.
- **`tools/`** — Codegen, replay utilities, load-test scaffolding.
- **`alembic/`** — Postgres migrations (canonical, config, bronze, identity_mirror, quarantine, staging, audit).
- **`dbt/`** — BigQuery dbt project (`canonical_history.*` models + freshness tests).
- **`tests/`** — Cross-service integration tests and e2e suites.

For schemas/contracts/infra in detail, see the directory-level `README.md` in each.

---

## When working in this repo (Claude Code)

1. **Read the root `CLAUDE.md`** at session start. It carries project-wide invariants.
2. **Move into the service or lib directory** you're working in. Claude Code lazy-loads that directory's `CLAUDE.md` automatically.
3. **Refer to the directory's `README.md`** for the EPE block / interface contract.
4. **Refer to the current slice doc** in `docs/slices/` for the active build target. The slice doc is the source of truth for what to build in this iteration.
5. **When uncertain**, ask before coding. See root `CLAUDE.md` for the "when uncertain" rule.
