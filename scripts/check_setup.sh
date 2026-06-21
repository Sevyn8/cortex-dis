#!/bin/bash
# ============================================================================
# check_setup.sh
#
# Pre-flight checks for the DIS local development environment.
# Run at the start of every Claude Code session and any time something feels
# off. Catches setup drift (stack down, env vars missing, deps out of sync,
# port conflicts) before code work begins.
#
# Usage:
#   ./scripts/check_setup.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#
# Output: PASS / FAIL / SKIP per check, with hints on failure.
# ============================================================================

set -uo pipefail

# Colour codes (skip if NO_COLOR is set or not a TTY)
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    GREEN=$'\033[0;32m'
    RED=$'\033[0;31m'
    YELLOW=$'\033[1;33m'
    CYAN=$'\033[0;36m'
    BOLD=$'\033[1m'
    RESET=$'\033[0m'
else
    GREEN=""; RED=""; YELLOW=""; CYAN=""; BOLD=""; RESET=""
fi

TOTAL_PASS=0
TOTAL_FAIL=0

pass() { echo "  ${GREEN}PASS${RESET}  $1"; TOTAL_PASS=$((TOTAL_PASS + 1)); }
fail() {
    echo "  ${RED}FAIL${RESET}  $1"
    [[ -n "${2:-}" ]] && echo "        ${YELLOW}hint:${RESET} $2"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
}
skip() {
    echo "  ${YELLOW}SKIP${RESET}  $1"
    [[ -n "${2:-}" ]] && echo "        ${YELLOW}reason:${RESET} $2"
}
section() { echo ""; echo "${BOLD}${CYAN}=== $1 ===${RESET}"; }

# ----------------------------------------------------------------------------
# Find project root (directory containing pyproject.toml)
# ----------------------------------------------------------------------------

PROJECT_ROOT=""
SEARCH_DIR="$(pwd)"
while [[ "$SEARCH_DIR" != "/" ]]; do
    if [[ -f "$SEARCH_DIR/pyproject.toml" ]]; then
        PROJECT_ROOT="$SEARCH_DIR"
        break
    fi
    SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done

if [[ -z "$PROJECT_ROOT" ]]; then
    echo "${RED}ERROR:${RESET} could not find project root (no pyproject.toml in any parent directory)."
    echo "Run this script from inside the ithina-dis repo."
    exit 1
fi

cd "$PROJECT_ROOT"
[ -f ".env" ] && set -a && . ".env" && set +a
echo "${BOLD}check_setup.sh${RESET} — DIS local devbox pre-flight"
echo "Project root: $PROJECT_ROOT"

# ----------------------------------------------------------------------------
# Tier 1 — Environment (tools, structure)
# ----------------------------------------------------------------------------

section "Tier 1: Environment"

# Required host tools
for tool in python3 uv psql docker git curl jq; do
    if command -v "$tool" >/dev/null 2>&1; then
        pass "$tool found on PATH"
    else
        fail "$tool not found" "install $tool and ensure it is on PATH"
    fi
done

# docker compose v2 subcommand
if docker compose version >/dev/null 2>&1; then
    pass "docker compose available"
else
    fail "docker compose not available" "install docker-compose-plugin"
fi

# alembic and dbt in the workspace venv
if [[ -x ".venv/bin/alembic" ]]; then
    pass "alembic present in .venv"
else
    fail "alembic not in .venv/bin/" "run: uv sync"
fi

if [[ -x ".venv/bin/dbt" ]]; then
    pass "dbt present in .venv"
else
    fail "dbt not in .venv/bin/" "run: uv sync"
fi

# Python pin
if [[ -f ".python-version" ]]; then
    pinned=$(cat .python-version)
    pass ".python-version pinned to $pinned"
else
    fail ".python-version missing" "run: uv python pin 3.12"
fi

# Required top-level dirs
for dir in services libs schemas contracts infra tools alembic dbt tests docs; do
    if [[ -d "$dir" ]]; then
        pass "directory exists: $dir"
    else
        fail "directory missing: $dir" "check git status or re-run local-setup.md §A.4"
    fi
done

# Required top-level files
for file in pyproject.toml docker-compose.yml Makefile .env.example .gitignore alembic.ini; do
    if [[ -f "$file" ]]; then
        pass "file exists: $file"
    else
        fail "file missing: $file" "check git status"
    fi
done

# .env present (loaded earlier; check explicitly)
if [[ -f ".env" ]]; then
    pass ".env file present"
else
    fail ".env file missing" "run: cp .env.example .env"
fi

# Pub/Sub contracts placed
pubsub_schema_count=$(find contracts/pubsub -maxdepth 1 -name "*.schema.json" 2>/dev/null | wc -l)
if [[ "$pubsub_schema_count" -ge 6 ]]; then
    pass "Pub/Sub schemas present: $pubsub_schema_count in contracts/pubsub/"
else
    fail "expected 6 Pub/Sub schemas in contracts/pubsub/, found $pubsub_schema_count" "copy schemas from prep-docs"
fi

# Topic creation script
if [[ -f "tools/local/create_topics.py" ]]; then
    pass "tools/local/create_topics.py present"
else
    fail "tools/local/create_topics.py missing" "see local-setup.md §A.6"
fi

# ----------------------------------------------------------------------------
# Tier 2 — Services (Docker stack)
# ----------------------------------------------------------------------------

section "Tier 2: Services"

if docker info >/dev/null 2>&1; then
    pass "Docker daemon running"
else
    fail "Docker daemon not running" "run: sudo systemctl start docker"
fi

# Each container in the DIS stack (core infra + Slice 2 fakes)
for container in ithina-dis-postgres-1 ithina-dis-pubsub-1 ithina-dis-gcs-1 ithina-dis-redis-1 \
                 ithina-dis-customer-master-1 ithina-dis-identity-service-fake-1; do
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$container"; then
        pass "container running: $container"
    else
        fail "container not running: $container" "run: make run-local"
    fi
done

# Postgres health
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "ithina-dis-postgres-1"; then
    health=$(docker inspect --format='{{.State.Health.Status}}' ithina-dis-postgres-1 2>/dev/null || echo "no-healthcheck")
    case "$health" in
        healthy)   pass "Postgres reports healthy" ;;
        starting)  fail "Postgres still starting" "wait a few seconds and re-run" ;;
        unhealthy) fail "Postgres reports unhealthy" "check: docker logs ithina-dis-postgres-1" ;;
        *)         skip "Postgres health: $health" ;;
    esac
fi

# ----------------------------------------------------------------------------
# Tier 3 — Connectivity (Postgres, Pub/Sub, GCS, Redis)
# ----------------------------------------------------------------------------

section "Tier 3: Connectivity"

# Port 5433 (DIS Postgres) reachable
if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h localhost -p "${POSTGRES_PORT:-5433}" -U "${POSTGRES_USER:-ithina_dis_user}" >/dev/null 2>&1; then
        pass "Postgres accepting connections on localhost:${POSTGRES_PORT:-5433}"
    else
        fail "Postgres not accepting connections on localhost:${POSTGRES_PORT:-5433}" "verify container is up; check for port conflict"
    fi
else
    skip "pg_isready not available"
fi

# POSTGRES_URL set + queryable
if [[ -n "${POSTGRES_URL:-}" ]]; then
    pass "POSTGRES_URL is set"
    # Strip +psycopg for libpq compatibility
    psql_url="${POSTGRES_URL/postgresql+psycopg/postgresql}"
    if psql "$psql_url" -c "SELECT 1;" >/dev/null 2>&1; then
        pass "psql can SELECT 1 from ithina_dis_db"
    else
        fail "psql cannot connect to ithina_dis_db" "check POSTGRES_URL and that container is running"
    fi
else
    fail "POSTGRES_URL not set" "ensure .env is loaded; check .env file content"
fi

# Pub/Sub emulator reachable; 6 topics present
if [[ -n "${PUBSUB_EMULATOR_HOST:-}" ]]; then
    pass "PUBSUB_EMULATOR_HOST is set: $PUBSUB_EMULATOR_HOST"
    pubsub_project="${PUBSUB_PROJECT_ID:-local-dis}"
    topic_count=$(curl -s "http://${PUBSUB_EMULATOR_HOST}/v1/projects/${pubsub_project}/topics" 2>/dev/null | jq '.topics | length' 2>/dev/null || echo "0")
    if [[ "$topic_count" -ge 6 ]]; then
        pass "Pub/Sub emulator: $topic_count topics present in project $pubsub_project"
    else
        fail "Pub/Sub emulator: expected 6 topics, found $topic_count" "run: make topics-create"
    fi
else
    fail "PUBSUB_EMULATOR_HOST not set" "ensure .env is loaded"
fi

# GCS emulator reachable
if [[ -n "${STORAGE_EMULATOR_HOST:-}" ]]; then
    pass "STORAGE_EMULATOR_HOST is set: $STORAGE_EMULATOR_HOST"
    if curl -s -o /dev/null -w "%{http_code}" "${STORAGE_EMULATOR_HOST}/storage/v1/b" 2>/dev/null | grep -q "^200$"; then
        pass "GCS emulator reachable"
    else
        fail "GCS emulator unreachable at ${STORAGE_EMULATOR_HOST}" "check container ithina-dis-gcs-1"
    fi
else
    fail "STORAGE_EMULATOR_HOST not set" "ensure .env is loaded"
fi

# Redis reachable
if [[ -n "${REDIS_URL:-}" ]]; then
    pass "REDIS_URL is set"
    if docker compose exec -T redis redis-cli PING 2>/dev/null | grep -qx "PONG"; then
        pass "Redis responds PONG"
    else
        fail "Redis not responding" "check container ithina-dis-redis-1"
    fi
else
    fail "REDIS_URL not set" "ensure .env is loaded"
fi

# ----------------------------------------------------------------------------
# Tier 4 — Customer Master read access (Mirror Sync DB-pull mode)
# ----------------------------------------------------------------------------

section "Tier 4: Customer Master read access"

# CM_DB_URL must be set
if [[ -n "${CM_DB_URL:-}" ]]; then
    pass "CM_DB_URL is set"
else
    fail "CM_DB_URL not set" "ensure .env has CM_DB_URL (see .env.example)"
fi

# CM Postgres reachable
if [[ -n "${CM_DB_URL:-}" ]] && command -v pg_isready >/dev/null 2>&1; then
    cm_host="${CM_DB_HOST:-localhost}"
    cm_port="${CM_DB_PORT:-5432}"
    if pg_isready -h "$cm_host" -p "$cm_port" >/dev/null 2>&1; then
        pass "Customer Master Postgres accepting connections on ${cm_host}:${cm_port}"
    else
        fail "Customer Master Postgres not reachable on ${cm_host}:${cm_port}" \
             "start Customer Master devbox separately (its own docker-compose)"
    fi
fi

# dis_mirror_reader can connect
if [[ -n "${CM_DB_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
    if psql "$CM_DB_URL" -c "SELECT 1;" >/dev/null 2>&1; then
        pass "dis_mirror_reader can connect to Customer Master"
    else
        fail "dis_mirror_reader cannot connect to Customer Master" \
             "run infra/customer-master/create-dis-mirror-reader.sql against CM as superuser; verify CM_DB_URL"
    fi
fi

# RLS smoke test: no-GUC reads return 0 (proves RLS is enforced for this role)
if [[ -n "${CM_DB_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
    count_no_guc=$(psql "$CM_DB_URL" -tAc \
        "SELECT set_config('app.tenant_id', NULL, FALSE); \
         SELECT set_config('app.user_type', NULL, FALSE); \
         SELECT COUNT(*) FROM core.tenants;" 2>/dev/null | tail -n 1)
    if [[ "$count_no_guc" == "0" ]]; then
        pass "Customer Master RLS enforced (no-GUC read returns 0)"
    elif [[ -z "$count_no_guc" ]]; then
        skip "RLS no-GUC check (could not query core.tenants)"
    else
        fail "Customer Master RLS may be bypassed (no-GUC read returned $count_no_guc rows)" \
             "verify dis_mirror_reader is NOBYPASSRLS; re-run create-dis-mirror-reader.sql"
    fi
fi

# PLATFORM smoke test: reads succeed under correct GUCs
if [[ -n "${CM_DB_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
    count_platform=$(psql "$CM_DB_URL" -tAc \
        "BEGIN; \
         SELECT set_config('app.tenant_id', NULL, TRUE); \
         SELECT set_config('app.user_type', 'PLATFORM', TRUE); \
         SELECT COUNT(*) FROM core.tenants; \
         ROLLBACK;" 2>/dev/null | tail -n 2 | head -n 1)
    if [[ -n "$count_platform" && "$count_platform" =~ ^[0-9]+$ ]]; then
        if [[ "$count_platform" -gt 0 ]]; then
            pass "Customer Master PLATFORM read returns $count_platform tenants"
        else
            skip "PLATFORM read returned 0 tenants (Customer Master may be empty)"
        fi
    else
        skip "PLATFORM smoke test (could not parse count)"
    fi
fi

# ----------------------------------------------------------------------------
# Tier 5 — DB state (migrations, role posture)
# ----------------------------------------------------------------------------

section "Tier 5: DB state"

if command -v uv >/dev/null 2>&1 && [[ -f "alembic.ini" ]]; then
    if uv run alembic current >/dev/null 2>&1; then
        current=$(uv run alembic current 2>&1 | tail -n 1)
        if [[ -z "$current" || "$current" == *"INFO"* ]]; then
            pass "alembic current runs (clean base state, or no migrations applied)"
        else
            pass "alembic current: $current"
        fi
    else
        fail "alembic current failed" "check POSTGRES_URL and alembic/env.py"
    fi
else
    skip "alembic check (uv or alembic.ini missing)"
fi

# Role posture: DIS uses RLS heavily. Application role must NOT bypass RLS.
if [[ -n "${POSTGRES_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
    role_url="${POSTGRES_URL/postgresql+psycopg/postgresql}"
    role_attrs=$(psql "$role_url" -tAc \
        "SELECT rolsuper::int::text || ',' || rolbypassrls::int::text FROM pg_roles WHERE rolname = '${POSTGRES_USER:-ithina_dis_user}';" \
        2>/dev/null)
    if [[ "$role_attrs" == "0,0" ]]; then
        pass "app role '${POSTGRES_USER:-ithina_dis_user}' is NOSUPERUSER NOBYPASSRLS"
    elif [[ -z "$role_attrs" ]]; then
        fail "could not query pg_roles" "verify role exists and POSTGRES_URL is correct"
    else
        flags=""
        IFS=',' read -r is_super is_bypass <<< "$role_attrs"
        [[ "$is_super" == "1" ]] && flags="${flags}SUPERUSER "
        [[ "$is_bypass" == "1" ]] && flags="${flags}BYPASSRLS "
        fail "app role has elevated privileges (${flags% }); RLS will be silently bypassed" "as superuser, run: ALTER ROLE ${POSTGRES_USER:-ithina_dis_user} NOSUPERUSER NOBYPASSRLS;"
    fi
else
    skip "role attribute check (POSTGRES_URL or psql missing)"
fi

# CSD-03 protection: role must NOT have rolconfig (default search_path masks bugs locally)
if [[ -n "${POSTGRES_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
    role_url="${POSTGRES_URL/postgresql+psycopg/postgresql}"
    rolconfig=$(psql "$role_url" -tAc \
        "SELECT COALESCE(array_to_string(rolconfig, ','), '') FROM pg_roles WHERE rolname = '${POSTGRES_USER:-ithina_dis_user}';" \
        2>/dev/null)
    if [[ -z "$rolconfig" ]]; then
        pass "app role has no rolconfig (schema-qualification regressions will surface)"
    else
        fail "app role has rolconfig set: ${rolconfig}" "as superuser, run: ALTER ROLE ${POSTGRES_USER:-ithina_dis_user} RESET search_path;"
    fi
else
    skip "rolconfig check (POSTGRES_URL or psql missing)"
fi

# ----------------------------------------------------------------------------
# Tier 6 — Code state (deps, dbt config)
# ----------------------------------------------------------------------------

section "Tier 6: Code state"

# uv.lock in sync
if uv lock --check >/dev/null 2>&1; then
    pass "uv.lock is in sync with pyproject.toml"
else
    fail "uv.lock out of sync" "run: uv sync"
fi

# .venv exists
if [[ -d ".venv" ]]; then
    pass ".venv exists"
else
    fail ".venv missing" "run: uv sync"
fi

# dbt config (parse-only check; doesn't require BQ access)
if [[ -f "dbt/dbt_project.yml" ]]; then
    pass "dbt/dbt_project.yml present"
    if [[ -f "$HOME/.dbt/profiles.yml" ]]; then
        pass "~/.dbt/profiles.yml present"
    else
        fail "~/.dbt/profiles.yml missing" "see local-setup.md §A.8"
    fi
else
    fail "dbt/dbt_project.yml missing" "run: uv run dbt init"
fi

# pytest collection (only if any test_*.py files exist)
test_files=$(find tests services libs -name "test_*.py" 2>/dev/null | wc -l)
if [[ "$test_files" -eq 0 ]]; then
    skip "pytest collection (no tests yet)"
else
    if uv run pytest --collect-only -q >/dev/null 2>&1; then
        pass "pytest can collect tests"
    else
        fail "pytest collection failed" "run: uv run pytest --collect-only ; fix import errors"
    fi
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

section "Summary"

total=$((TOTAL_PASS + TOTAL_FAIL))
echo "  ${GREEN}Passed:${RESET} $TOTAL_PASS / $total"
if [[ "$TOTAL_FAIL" -gt 0 ]]; then
    echo "  ${RED}Failed:${RESET} $TOTAL_FAIL"
    echo ""
    echo "${RED}${BOLD}Setup is not ready. Fix the FAIL items above before proceeding.${RESET}"
    exit 1
else
    echo ""
    echo "${GREEN}${BOLD}All checks passed. Setup is ready.${RESET}"
    exit 0
fi
