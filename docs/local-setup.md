# DIS Local Setup

**Audience:** human operator setting up a Zorin/Ubuntu 22.04 devbox for DIS development.
**Goal:** a working local devbox where `make run-local` brings up the entire stack and `scripts/check_setup.sh` reports 100% green; plus a path to connect to dev GCP when needed.
**Status:** mutates as the build progresses.

---

## A. Local devbox

### A.0 Starting state

**Why.** Calibrate against what's actually on the devbox before touching anything. Saves a wasted install if a tool is already present and prevents a missing prerequisite from surfacing mid-setup.

- Zorin OS 17.3 (Ubuntu 22.04 base).
- Already installed (verified via `check_setup.sh`): uv 0.11+, docker + compose v2, git, gh CLI (authenticated), gcloud (authenticated with ADC), psql 15, bq, jq, make, Cloud SQL Proxy v2, Terraform.
- Python 3.12 already installed via uv (no fresh download needed).
- Required to install: yq, pre-commit.
- NOT installed on host (intentional, all live in workspace venv): Alembic, dbt-bigquery, Python 3.12-tied dependencies. uv manages all of these.

**Disk space.** Stack pulls ~3-5 GB in Docker images plus ~1-2 GB in `.venv`. Confirm 8+ GB free before proceeding:

```bash
df -h ~ | head -2
```

### A.1 Install host tools

**Why.** `yq` reads/edits the YAML configs scattered through this repo (docker-compose, GitHub Actions, dbt profiles, k8s manifests). `pre-commit` runs git-hook checks at commit time to catch formatting drift and accidental large files before they enter history — important when Claude Code commits frequently.

```bash
sudo wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
sudo chmod +x /usr/local/bin/yq
yq --version

uv tool install pre-commit
pre-commit --version
```

Expected: `yq` 4.x, `pre-commit` 3.x or 4.x.

### A.2 Ensure Python 3.12 is available to uv

**Why.** `pyproject.toml` declares `requires-python = ">=3.12"`; uv refuses to install dependencies without it. Matching Customer Master's Python version keeps Sevyn8 projects consistent in CI and dev tooling.

```bash
uv python list | grep 3.12
```

If a `cpython-3.12.x-...` entry is shown, skip. Otherwise:

```bash
uv python install 3.12
```

### A.3 Workspace scaffolding

**Why.** Lays down the contract for the repo: which Python version, which dependencies, which directories. uv reads `pyproject.toml` to resolve packages; the directory skeleton matches `repo-structure.md` so future code lands in the documented places without guessing.

```bash
cd ~/ithina-dis

cat > pyproject.toml <<'PYPROJECT'
[project]
name = "ithina-dis"
version = "0.0.0"
description = "DIS — Data Integration System for the Ithina Data Platform"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
  # HTTP services / clients
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "httpx>=0.27",

  # Postgres
  "sqlalchemy[asyncio]>=2.0.36",
  "psycopg[binary]>=3.2",
  "alembic>=1.14",

  # Validation engine (decisions.md D4a)
  "pandera[polars]>=0.20",
  "polars>=1.0",
  "pyarrow>=17",

  # CSV / SQL preview (decisions.md D16)
  "duckdb>=1.0",

  # GCP clients
  "google-cloud-pubsub>=2.23",
  "google-cloud-storage>=2.18",
  "google-cloud-bigquery>=3.25",
  "google-cloud-kms>=3.1",
  "google-cloud-secret-manager>=2.20",
  "google-cloud-logging>=3.10",

  # Auth (Customer Master JWT verification)
  "pyjwt[crypto]>=2.8",

  # Pydantic + config
  "pydantic[email]>=2.9",
  "pydantic-settings>=2.6",

  # BigQuery models (decisions.md D23)
  "dbt-bigquery>=1.8,<1.10",

  # IDs (UUIDv7)
  "uuid-utils>=0.9",

  # Retry / reliability
  "tenacity>=9",

  # Fast JSON
  "orjson>=3.10",

  # Tracing (trace_id propagation per architecture.md)
  "opentelemetry-api>=1.27",
  "opentelemetry-sdk>=1.27",
  "opentelemetry-exporter-gcp-trace>=1.7",

  # Structured logging (matches Customer Master)
  "python-json-logger>=2.0,<4.0",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=5",
  "ruff>=0.6",
  "mypy>=1.13",
  "faker>=30",
  "types-pyyaml",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
package = false

[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "TID"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
PYPROJECT

echo "3.12" > .python-version
echo "# DIS — Data Integration System" > README.md

cat > .gitignore <<'GITIGNORE'
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
*.egg-info/
dist/
build/
.env
.env.local
.idea/
.vscode/
*.swp
.DS_Store
# dbt
dbt/target/
dbt/dbt_packages/
dbt/logs/
GITIGNORE

# Directory skeleton
mkdir -p services libs ui \
         schemas/postgres schemas/bigquery \
         contracts/pubsub contracts/identity-service \
         infra/terraform/envs/dev infra/terraform/envs/staging infra/terraform/envs/prod \
         infra/k8s infra/local \
         tools/local \
         alembic \
         dbt \
         tests/integration tests/e2e \
         docs/slices docs/runbooks \
         scripts \
         .github/workflows
```

Notes on what the above does:

- `[tool.uv] package = false` — the repo root is a workspace manager, not a buildable Python package. Without this, `uv sync` fails complaining about a missing src directory.
- No `[tool.uv.workspace]` block yet — would require `services/*` and `libs/*` to already contain `pyproject.toml` files. Added later when services/libs are scaffolded.
- `mkdir` includes `scripts/` (for `check_setup.sh`) and `infra/local/` (for Postgres init SQL).

### A.4 Download all dependencies

**Why.** Hydrates the `.venv/` with all ~150 packages declared in `pyproject.toml` and writes `uv.lock` (the reproducible version pin). Every later step depends on these packages being present.

```bash
cd ~/ithina-dis
uv sync
```

First run takes 1-3 minutes (~150 packages). Expected exit: clean, with `.venv/` and `uv.lock` created.

### A.5 docker-compose stack

**Why.** The local stack replaces every external dependency (Postgres, Pub/Sub, GCS, Redis) with an emulator or container that speaks the same protocol. This is what makes services runnable locally without a GCP project. Two non-obvious choices documented inline: port 5433 (not 5432) to coexist with Customer Master's Postgres, and two Postgres roles to enforce RLS correctly.

Postgres uses host port **5433** (not the default 5432) to avoid clashing with Customer Master's local Postgres.

```bash
cd ~/ithina-dis

cat > docker-compose.yml <<'COMPOSE'
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: ithina_dis_admin
      POSTGRES_PASSWORD: ithina_dis_admin_password
      POSTGRES_DB: ithina_dis_db
    ports:
      - "5433:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./infra/local/postgres-init.sql:/docker-entrypoint-initdb.d/01-init-app-role.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ithina_dis_admin -d ithina_dis_db"]
      interval: 5s
      timeout: 3s
      retries: 10

  pubsub:
    image: gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators
    command: gcloud beta emulators pubsub start --host-port=0.0.0.0:8085 --project=local-dis
    ports:
      - "8085:8085"

  gcs:
    image: fsouza/fake-gcs-server:latest
    command: -scheme http -public-host localhost:4443 -port 4443
    ports:
      - "4443:4443"
    volumes:
      - gcs-data:/storage

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

volumes:
  postgres-data:
  gcs-data:
COMPOSE
```

**Two Postgres roles.** Postgres image bootstraps `POSTGRES_USER` as a superuser unconditionally. DIS needs an application role with `NOSUPERUSER NOBYPASSRLS` (so RLS enforcement is real, not silently bypassed). Resolution:

- **`ithina_dis_admin`** — bootstrap superuser. Used by Alembic for migrations only.
- **`ithina_dis_user`** — application role. NOSUPERUSER NOBYPASSRLS. Used by all service code at runtime.

The application role is created by an init SQL script that runs once on first container start.

### A.6 Postgres init script (app role)

**Why.** Postgres's image makes `POSTGRES_USER` a superuser unconditionally. DIS uses Row-Level Security (RLS) to isolate tenants; a superuser app role would silently bypass RLS and the bug only surfaces in cloud. This init script creates a separate NOSUPERUSER NOBYPASSRLS role for service code, matching production posture from day one.

```bash
cd ~/ithina-dis

cat > infra/local/postgres-init.sql <<'SQL'
-- Runs once on first container start (fresh volume).
-- Creates the application role with production-correct posture:
--   NOSUPERUSER  → role is NOT a superuser
--   NOBYPASSRLS  → RLS policies apply to this role; queries that lack
--                  app.tenant_id context will return empty results, not
--                  silently leak cross-tenant data.

CREATE ROLE ithina_dis_user
  WITH LOGIN
       NOSUPERUSER
       NOBYPASSRLS
       NOCREATEDB
       NOCREATEROLE
       PASSWORD 'ithina_dis_password';

GRANT CONNECT ON DATABASE ithina_dis_db TO ithina_dis_user;
GRANT USAGE ON SCHEMA public TO ithina_dis_user;
SQL
```

### A.7 Pub/Sub topic creation script

**Why.** The Pub/Sub emulator starts empty. Real GCP gets topics from Terraform; locally they need to be created on each fresh stack. The script is idempotent so it can be safely re-run, and refuses to run against real GCP (guard against accidental project pollution).

```bash
cd ~/ithina-dis

cat > tools/local/create_topics.py <<'PY'
"""Create the DIS Pub/Sub topics on the local emulator.

Idempotent: existing topics are skipped. Refuses to run against real Pub/Sub.
"""

import os
import sys

from google.cloud import pubsub_v1
from google.api_core.exceptions import AlreadyExists


TOPICS = [
    "ingress.ready",
    "ingress.resubmit",
    "identity.changed",
    "quarantine",
    "mapping.changed",
    "pipeline.dlq",
]


def main() -> int:
    if not os.getenv("PUBSUB_EMULATOR_HOST"):
        print("PUBSUB_EMULATOR_HOST not set; refusing to run against real Pub/Sub.", file=sys.stderr)
        return 2

    project = os.getenv("PUBSUB_PROJECT_ID", "local-dis")
    publisher = pubsub_v1.PublisherClient()

    for topic_name in TOPICS:
        topic_path = publisher.topic_path(project, topic_name)
        try:
            publisher.create_topic(request={"name": topic_path})
            print(f"created: {topic_name}")
        except AlreadyExists:
            print(f"exists:  {topic_name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
PY

touch tools/local/__init__.py
```

### A.8 .env file (loaded by Makefile)

**Why.** Centralises the connection strings, ports, and bucket names that service code and tooling need. `.env.example` is the committed template; `.env` is the gitignored local copy. Splitting app vs admin Postgres URLs prevents Alembic (which needs DDL privileges) and service code (which must not have them) from sharing a role.

```bash
cd ~/ithina-dis

cat > .env.example <<'ENV'
# Local devbox env vars. Copy to .env (gitignored) and adjust if needed.
# Defaults match the docker-compose stack.

# GCP (set when working against dev/staging/prod)
GCP_PROJECT_DEV=ithina-dis-dev
GCP_REGION=asia-south1

# Local Postgres — app role (used by service code)
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_USER=ithina_dis_user
POSTGRES_PASSWORD=ithina_dis_password
POSTGRES_DB=ithina_dis_db
POSTGRES_URL=postgresql+psycopg://ithina_dis_user:ithina_dis_password@localhost:5433/ithina_dis_db

# Admin role (for migrations only; NOT for service code)
POSTGRES_ADMIN_URL=postgresql+psycopg://ithina_dis_admin:ithina_dis_admin_password@localhost:5433/ithina_dis_db

# Pub/Sub emulator
PUBSUB_EMULATOR_HOST=localhost:8085
PUBSUB_PROJECT_ID=local-dis

# GCS emulator (fake-gcs-server)
STORAGE_EMULATOR_HOST=http://localhost:4443
GCS_BUCKET_BRONZE=ithina-bronze-raw
GCS_BUCKET_REPLAY=ithina-replay-staging
GCS_BUCKET_DLQ=ithina-dlq

# Redis (identity cache)
REDIS_URL=redis://localhost:6379

# Identity Service fake (scaffolded under libs/dis-testing)
IDENTITY_SERVICE_URL=http://localhost:8081

# Customer Master fake (scaffolded under libs/dis-testing)
CUSTOMER_MASTER_URL=http://localhost:8082
ENV

cp .env.example .env
```

### A.9 Makefile

**Why.** The single entry point for daily operations: `make run-local`, `make check`, `make test`, `make db-migrate`. `include .env` + `export` at the top means every target sees the env vars without manual sourcing. Newcomers (human or Claude Code) discover what's runnable via `make help`.

```bash
cd ~/ithina-dis

cat > Makefile <<'MAKE'
include .env
export

.PHONY: help run-local stop-local reset-local test lint format db-migrate db-revision db-reset topics-create dbt-debug dbt-run sync clean check

help:
	@echo "DIS local commands:"
	@echo "  make sync          - install all workspace dependencies"
	@echo "  make run-local     - bring up docker stack + create topics + apply migrations"
	@echo "  make stop-local    - stop docker stack (data persists)"
	@echo "  make reset-local   - stop + wipe all data volumes"
	@echo "  make check         - run pre-flight checks (scripts/check_setup.sh)"
	@echo "  make test          - run all tests"
	@echo "  make lint          - run ruff lint"
	@echo "  make format        - run ruff format"
	@echo "  make db-migrate    - apply pending Alembic migrations"
	@echo "  make db-revision   - create a new Alembic migration"
	@echo "  make db-reset      - drop and recreate Postgres schemas (destructive)"
	@echo "  make topics-create - create Pub/Sub topics on the emulator"
	@echo "  make dbt-debug     - validate dbt config against current target"
	@echo "  make dbt-run       - run dbt models"

sync:
	uv sync

run-local: sync
	docker compose up -d
	@echo "Waiting for Postgres to be ready..."
	@until docker compose exec -T postgres pg_isready -U ithina_dis_admin -d ithina_dis_db >/dev/null 2>&1; do sleep 1; done
	@echo "Postgres ready."
	$(MAKE) topics-create
	$(MAKE) db-migrate
	@echo "Local stack up. See 'docker compose ps' for status."

stop-local:
	docker compose down

reset-local:
	docker compose down -v
	@echo "Volumes wiped. Run 'make run-local' to start clean."

check:
	./scripts/check_setup.sh

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

db-migrate:
	uv run alembic upgrade head

db-revision:
	@read -p "Migration message: " msg; \
	uv run alembic revision --autogenerate -m "$$msg"

db-reset:
	docker compose exec -T postgres psql -U ithina_dis_admin -d postgres -c "DROP DATABASE IF EXISTS ithina_dis_db;"
	docker compose exec -T postgres psql -U ithina_dis_admin -d postgres -c "CREATE DATABASE ithina_dis_db;"
	$(MAKE) db-migrate

topics-create:
	uv run python tools/local/create_topics.py

dbt-debug:
	cd dbt && uv run dbt debug

dbt-run:
	cd dbt && uv run dbt run

clean:
	rm -rf .venv .ruff_cache .mypy_cache .pytest_cache **/__pycache__ **/*.egg-info dbt/target dbt/logs
MAKE
```

The `include .env` + `export` lines at the top mean every Makefile target sees `POSTGRES_URL`, `PUBSUB_EMULATOR_HOST`, etc. without manual sourcing.

### A.10 Initialize Alembic

**Why.** Alembic owns DIS Postgres schema migrations (`decisions.md` D23). The init step writes `env.py` and `script.py.mako`; the URL override points it at the admin role (the app role lacks privileges to create tables).

```bash
cd ~/ithina-dis
uv run alembic init alembic
```

This writes `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/`, and `alembic.ini` at the repo root. It populates the pre-existing `alembic/` directory without complaint.

Point Alembic at the admin role URL (only role with privileges to run DDL):

```bash
sed -i 's|^sqlalchemy.url = .*|sqlalchemy.url = postgresql+psycopg://ithina_dis_admin:ithina_dis_admin_password@localhost:5433/ithina_dis_db|' alembic.ini
```

### A.11 Initialize dbt

**Why.** dbt owns the BigQuery side of the data platform: `canonical_history.*` models and freshness tests (`decisions.md` D23). The init produces a scaffolded project; flattening it removes the redundant `dis/` subdirectory. `profiles.yml` uses ADC (`gcloud auth application-default login`) so credentials live outside the repo.

```bash
cd ~/ithina-dis/dbt
uv run dbt init dis --skip-profile-setup
```

dbt scaffolds into `dbt/dis/`. Flatten so `dbt/` IS the project root (so `cd dbt && dbt run` works without an extra level):

```bash
cd ~/ithina-dis
shopt -s dotglob
mv dbt/dis/* dbt/
shopt -u dotglob
rmdir dbt/dis
rm -f dbt/.gitignore  # already covered by root .gitignore
```

dbt profile (global to your user, not repo-local):

```bash
mkdir -p ~/.dbt
cat > ~/.dbt/profiles.yml <<'YML'
dis:
  target: dev
  outputs:
    dev:
      type: bigquery
      method: oauth
      project: ithina-dis-dev
      dataset: canonical_history
      location: asia-south1
      threads: 4
      timeout_seconds: 300
YML
```

`method: oauth` uses your `gcloud auth application-default login` credentials. `dbt parse` works offline; `dbt run` needs real BigQuery (defer until the dev project exists).

### A.12 Identity Service and Customer Master fake stubs

**Why.** `.env` references `IDENTITY_SERVICE_URL` and `CUSTOMER_MASTER_URL`. These point at fakes that don't exist yet — Slice 1 builds them out. Empty placeholder modules now means the `.env` keys aren't dangling and the directory shape signals where the implementations will land.

Placeholders so `.env` references aren't dangling. Slice 1 fills them in:

```bash
cd ~/ithina-dis
mkdir -p libs/dis-testing/src/dis_testing/fakes
touch libs/dis-testing/src/dis_testing/__init__.py
touch libs/dis-testing/src/dis_testing/fakes/__init__.py

cat > libs/dis-testing/src/dis_testing/fakes/identity_service.py <<'PY'
"""Identity Service fake for local devbox.

Placeholder. Slice 1 (or wherever identity-service is built) fills this in
as a minimal FastAPI app exposing the four interface methods documented
in services/identity-service/README.md.
"""
PY

cat > libs/dis-testing/src/dis_testing/fakes/customer_master.py <<'PY'
"""Customer Master fake for local devbox.

Placeholder. Issues test JWTs and returns canned identity payloads.
Filled in when Slice 1 needs Customer Master verification end-to-end.
"""
PY
```

### A.13 Place Pub/Sub schemas

**Why.** The six Pub/Sub message schemas are Phase 0 frozen contracts (per `build-guide.md` Phase 0 deliverables). Landing them in `contracts/pubsub/` before any service code starts means every receiver and consumer codes against the same agreed-upon shapes; the schemas don't get re-litigated mid-build.

Copy the 12 frozen schema files (6 schemas + 6 examples) into `contracts/pubsub/`:

```bash
cp ~/Downloads/ithina-dis-prep-docs/contracts-dis-pubsub-schemas/*.json contracts/pubsub/
ls contracts/pubsub/
```

Adjust source path as needed.

### A.14 pre-commit configuration

**Why.** Defines which hooks run before every commit: ruff (lint + format), trailing whitespace, JSON/YAML syntax, merge-conflict markers, large-file blocks. Catches whole classes of mistakes that Claude Code can accidentally introduce in agentic mode. Installation deferred until git is initialised in §A.19.

```bash
cd ~/ithina-dis

cat > .pre-commit-config.yaml <<'PRECOMMIT'
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-json
      - id: check-merge-conflict
      - id: check-added-large-files
        args: [--maxkb=500]
PRECOMMIT
```

The git-hook install (`pre-commit install`) requires the repo to be a git repo. Defer that step until §A.18 (git init).

### A.15 Install the pre-flight check script

**Why.** `check_setup.sh` runs six tiers of checks (environment, services, connectivity, Customer Master read access, DB state, code state) in seconds. Used at the start of every Claude Code session and whenever something feels off; catches setup drift (stack down, env vars missing, role posture broken) before code work begins. Failures include hints with exact fix commands.

```bash
cd ~/ithina-dis
cp <path-to-published>/check_setup.sh scripts/check_setup.sh
chmod +x scripts/check_setup.sh
```

The script runs six tiers of checks: environment (tools, structure), services (containers), connectivity (Postgres, Pub/Sub, GCS, Redis), Customer Master read access, DB state (Alembic, role posture), code state (deps, dbt config). Output is PASS / FAIL / SKIP with hints.

### A.16 Bring up the stack

**Why.** First end-to-end run of `make run-local`: syncs deps, starts containers, waits for Postgres health, creates topics, runs migrations. Validates the whole §A.3 to §A.15 chain at once. First-time docker image pulls take a few minutes; subsequent runs are seconds.

```bash
cd ~/ithina-dis
make run-local
```

First run: 2-5 minutes for docker image pulls (~3-5 GB). Subsequent runs: 10-30 seconds.

Expected sequence:
1. `uv sync` (no-op if already synced).
2. `docker compose up -d` — 4 containers start.
3. Wait for Postgres healthy.
4. Init script creates `ithina_dis_user` role.
5. `make topics-create` — 6 Pub/Sub topics created.
6. `make db-migrate` — Alembic runs (no migrations yet, clean base state).

### A.17 Verify with check_setup.sh

**Why.** Sanity check that everything brought up by §A.16 is healthy and reachable. Expected 57/57 PASS means the local devbox is in production-correct posture (RLS-safe roles, 6 topics, 4 containers, CM read access verified healthy, deps in sync). Any FAIL has a directly actionable hint.

```bash
cd ~/ithina-dis
./scripts/check_setup.sh
```

Expected: 57/57 PASS, one SKIP (pytest, no tests yet).

If any FAIL appears, the script's hint shows how to fix.

### A.18 Place project docs

**Why.** Architecture docs (`architecture.md`, `decisions.md`, etc.) live in the repo so Claude Code can read them; root `CLAUDE.md` is auto-loaded by Claude Code at session start. Without these in place, Claude Code is working blind to the project's design decisions.

Copy architecture and design docs into `docs/`:

```bash
SRC=~/Downloads/ithina-dis-prep-docs
cd ~/ithina-dis/docs

cp $SRC/architecture.md .
cp $SRC/architecture.html .
cp $SRC/decisions.md .
cp $SRC/engineering-reference.md .
cp $SRC/repo-structure.md .
cp $SRC/build-guide.md .
cp $SRC/cost-estimate.md .
cp $SRC/local-setup.md .
cp $SRC/worked-example-streaming-consumer.md .

# Root CLAUDE.md goes at repo root, not in docs/
cd ~/ithina-dis
cp $SRC/CLAUDE.md .
ls docs/ CLAUDE.md
```

### A.19 Initialize git and push to GitHub

**Why.** Done last so the first commit captures a working, verified setup — not a sequence of broken intermediate states. Confirming the GitHub account before `gh repo create` prevents accidentally creating the repo under the wrong owner. `pre-commit install` writes git hooks now that `.git/` exists.

```bash
cd ~/ithina-dis
git init
git branch -m main

# Install pre-commit hooks now that .git/ exists
pre-commit install

# Stage and commit
git add .
git commit -m "Phase 0 scaffolding: workspace, docker stack, alembic, dbt, fakes, docs"

# Create private GitHub repo and push (verify the account is correct first)
gh repo create ithina-dis --private --source=. --remote=origin
git push -u origin main
```

Confirm the GitHub account before running `gh repo create` — `gh auth status` shows the active account.

### A.20 GitHub Actions CI

**Why.** Runs the same `ruff` and `pytest` checks on every push and pull request as locally. Catches the case where local-but-not-CI passes — usually a missing test dependency or platform-specific behaviour. Cheap insurance once the repo is on GitHub.

```bash
cd ~/ithina-dis

cat > .github/workflows/ci.yml <<'CI'
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv python install 3.12
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest
CI

git add .github/workflows/ci.yml
git commit -m "ci: ruff + pytest on push and PR"
git push
```

### A.21 Devbox ready

**Why.** Single-page checklist of what's now true on the devbox. Useful as a snapshot before opening Claude Code for Slice 1, and as the reference for diagnosing later setup drift.

At this point:
- uv workspace runs with all dependencies pinned.
- Python 3.12 active via uv.
- Docker stack runs with persistent volumes (Postgres, GCS).
- Postgres has two roles: admin (for migrations) and app (RLS-correct).
- Pub/Sub topics created.
- dbt scaffolded.
- pre-commit guards commits.
- GitHub Actions CI runs on push.
- Docs and root CLAUDE.md are in place.
- `check_setup.sh` runs in seconds and shows 57/57 PASS.

### A.22 Daily commands

**Why.** Quick reference for the commands you run repeatedly. Memorising `make run-local`, `make check`, `make db-migrate` is faster than re-reading the doc each session.

```bash
make run-local     # bring up stack + topics + migrations
make stop-local    # stop stack (data persists)
make reset-local   # stop + wipe data (clean slate)
make check         # run pre-flight checks
make test          # run all tests
make lint          # ruff lint
make format        # ruff format
make db-revision   # new Alembic migration (autogenerated)
make db-migrate    # apply pending migrations
make db-reset      # drop + recreate Postgres + re-migrate (destructive)
make dbt-debug     # validate dbt config
make dbt-run       # run dbt models
```

### A.23 Rescue commands

**Why.** When the stack misbehaves (container won't start, disk fills, deps drift), this is the first place to look. Each command targets a specific failure mode; ordered from least-destructive (inspect logs) to most-destructive (nuke Docker).

```bash
docker compose ps                              # what's running
docker compose logs -f postgres                # tail a stuck service
make reset-local && make run-local             # full reset (loses local data)
make clean && uv sync                          # rebuild workspace venv
docker system prune -af --volumes              # nuke Docker if disk is full
```

---

## B. GCP staging / dev environment

For when Phase 0 needs a real GCP project to point at. Defer until you actually need it.

### B.1 Project layout

Three GCP projects, one per environment:

| Project ID | Purpose |
|---|---|
| `ithina-dis-dev` | Shared dev; integration tests; mutable |
| `ithina-dis-staging` | Pre-production verification; immutable from dev |
| `ithina-dis-prod` | Production |

All under the Sevyn8 GCP organization. Region default: `asia-south1` (Mumbai). Customer Master lives separately in `ithina-retail-admin`; DIS calls Customer Master across project boundaries.

**Naming.** Cloud SQL database `ithina_dis_db`, app user `ithina_dis_user`, admin user `ithina_dis_admin`. Local devbox uses literal passwords; dev/staging/prod use Secret Manager-managed passwords, never literals.

### B.2 What Terraform provisions (per project)

- Cloud SQL Postgres 15 with the two roles and DIS schemas.
- Pub/Sub topics: `ingress.ready`, `ingress.resubmit`, `identity.changed`, `quarantine`, `mapping.changed`, `pipeline.dlq`. Subscriptions per consumer.
- GCS buckets: `ithina-bronze-raw`, `ithina-replay-staging`, `ithina-dlq`, with lifecycle policies.
- BigQuery datasets: `canonical_history`, `audit`. Tables per the dbt project.
- Cloud Scheduler jobs: nightly-batch, daily-compute (placeholders).
- Memorystore Redis (Basic, 1 GB) for identity-service cache.
- Service accounts: one per service, least-privilege.
- KMS keyring for per-tenant PII tokenization keys.
- Secret Manager entries for service credentials and the Customer Master JWT signing key.

Terraform lives in `~/ithina-dis/infra/terraform/`. Phase 0 deliverable: it applies cleanly to `ithina-dis-dev`.

### B.3 Bootstrap order

Once `~/ithina-dis/infra/terraform/` exists:

```bash
# Re-authenticate ADC for the new DIS project (currently scoped to ithina-retail-admin)
gcloud auth application-default login

gcloud projects create ithina-dis-dev --organization=<sevyn8-org-id>
gcloud beta billing projects link ithina-dis-dev --billing-account=<billing-account-id>
gcloud config set project ithina-dis-dev
gcloud auth application-default set-quota-project ithina-dis-dev

cd ~/ithina-dis/infra/terraform/envs/dev
terraform init
terraform plan
terraform apply
```

### B.4 Switch local services to dev GCP

Two modes:

- **Default (`local`):** services talk to docker-compose. This is §A.
- **`dev`:** services run locally but talk to dev GCP services.

To switch:

```bash
export DIS_TARGET=dev

gcloud auth application-default login

# Unset emulator env vars so client libs route to real GCP
unset PUBSUB_EMULATOR_HOST STORAGE_EMULATOR_HOST

# Cloud SQL Proxy v2 on port 5434 (avoids clashing with local Postgres on 5433)
cloud-sql-proxy ithina-dis-dev:asia-south1:dis-primary --port=5434 &
```

Use a separate `.env.dev` or inline overrides for the dev-target `POSTGRES_URL`. Stop the proxy with `kill %1` or by PID.

### B.5 Common pitfalls

- ADC currently scoped to `ithina-retail-admin`. Re-auth + `set-quota-project ithina-dis-dev` after the DIS project exists.
- `gcloud auth login` (CLI) and `gcloud auth application-default login` (client libraries) are different commands; both needed.
- Region locked to `asia-south1`. BigQuery dataset location cannot change after creation.
- Use v2 Cloud SQL Proxy (`cloud-sql-proxy`), not the deprecated v1 (`cloud_sql_proxy`).
- Don't run `make run-local` against `DIS_TARGET=dev`; the Makefile is hardwired to local docker.

---
