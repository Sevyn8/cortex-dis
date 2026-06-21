# DIS — Data Integration System

DIS is a multi-tenant retail data ETL platform on GCP. Tenants submit data via four ingress channels (CSV upload, API push, ERP CSV POST, reverse-API pull); DIS validates, maps, and writes to a canonical Postgres schema (Cloud SQL) and archives long-term to BigQuery.

Part of the Ithina Data Platform. Operates alongside Customer Master (identity/auth) and ROOS (the recommendation engine that reads from DIS).

**Beta scope:** 5 tenants × ~25 stores × ~5000 SKUs (~150K events/day).

---

## Quick start

```bash
# One-time devbox setup; see docs/local-setup.md for the full walkthrough.
make run-local       # docker stack + Pub/Sub topics + Alembic migrations
make check           # pre-flight checks (expect 52/52 PASS)
```

Day-to-day:

```bash
make test            # run all tests
make lint            # ruff lint
make db-migrate      # apply pending Alembic migrations
make stop-local      # stop docker stack
make reset-local     # nuke local data; start clean
```

`make help` lists every available target.

---

## Repository map

| Directory | Purpose |
|---|---|
| `services/` | Containerised services (receivers, identity, streaming consumer, drainers, dis-ui-server) |
| `libs/` | Shared Python libraries (dis-core, dis-canonical, dis-mapping, dis-validation, dis-rls, dis-audit, dis-pii, dis-storage, dis-testing) |
| `ui/` | DIS UI (TypeScript + React) |
| `schemas/` | SQL DDL: `postgres/<schema>/*.sql`, `bigquery/*.sql` |
| `contracts/` | Pub/Sub schemas, Identity Service gRPC, Customer Master JWT contract |
| `infra/` | Terraform per env, k8s manifests, local Postgres init |
| `tools/` | Operator tooling (Pub/Sub topic creation, replay, codegen) |
| `alembic/` | Postgres schema migrations |
| `dbt/` | BigQuery models (`canonical_history.*`) |
| `tests/` | Cross-service integration and e2e tests |
| `docs/` | Architecture, decisions, slices, runbooks |
| `scripts/` | Operator scripts (`check_setup.sh`) |
| `.github/` | CI workflows |

For directory trees in detail, see `docs/repo-structure.md`.

---

## Documentation

Start here:

- **`docs/architecture.md`** — the WHY: system context, modules, data flow.
- **`docs/architecture.html`** — visual diagram of the architecture.
- **`docs/decisions.md`** — indexed register of every architecture-level decision (D1-Dn).
- **`docs/engineering-reference.md`** — top-level index into per-directory references.
- **`docs/repo-structure.md`** — detailed directory trees.
- **`docs/build-guide.md`** — build phases, migration triggers, operator workflow.
- **`docs/cost-estimate.md`** — beta-scale infrastructure cost projection.
- **`docs/local-setup.md`** — devbox setup, step by step.

For Claude Code, the load-bearing files are:
- **`CLAUDE.md`** at repo root (auto-loaded every session) — project-wide invariants and conventions.
- **`docs/slices/`** — the active slice doc is the source of truth for what to build right now.

---

## Stack

Python 3.12 · FastAPI · Postgres 15 · BigQuery · Pub/Sub · GCS · Redis · Pandera · Polars · DuckDB · Alembic · dbt-bigquery · OpenTelemetry · uv (workspace manager).

GCP managed-first; everything runnable locally via emulators (Pub/Sub emulator, fake-gcs-server, dockerised Postgres + Redis).

---

## Status

Phase 0 (foundation) substantially complete: workspace, dependencies, local stack, schemas, contracts, services + libs scaffolded, docs, root commit on `main`. GitHub push and CI pending. Slice 1 (bootstrap Alembic migration) is the next work item.

For slice-by-slice build status, see `docs/build-guide.md`.
