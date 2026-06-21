# csv-ingest-worker
#
# A long-running Pub/Sub PULL consumer: it subscribes to csv.received, runs the
# ingest pipeline (tier-0 -> bronze -> PII gate -> publish ingress.ready), and in
# Cloud Run Service mode (RUN_HEALTH_SERVER=true) serves a readiness /healthz on
# the Cloud-Run-injected $PORT alongside the loop (slice 40a). Entrypoint:
# services/csv-ingest-worker/src/csv_ingest_worker/main.py (module main()).
#
# Mirrors the proven services/dis-ui-server/Dockerfile: the service is a uv-workspace
# member, so the build context is the REPO ROOT and EVERY declared workspace member
# directory is copied explicitly (uv refuses a workspace whose declared members are
# absent). Build with:
#
#   docker build -f terraform/docker/csv-ingest-worker.Dockerfile -t csv-ingest-worker .

FROM python:3.12-slim

# uv pinned to the minor the repo develops against (uv 0.11 locally).
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The workspace skeleton: root project + every declared member (libs, services).
# Explicit COPYs keep the image free of repo junk regardless of context contents.
COPY pyproject.toml uv.lock ./
COPY libs libs
COPY services/mirror-sync-consumer services/mirror-sync-consumer
COPY services/csv-ingest-worker services/csv-ingest-worker
COPY services/streaming-consumer services/streaming-consumer
COPY services/dis-ui-server services/dis-ui-server

# Install ONLY this service's dependency closure, locked (--frozen: the lock is
# authoritative; a drifted lock fails the build instead of resolving silently).
RUN uv sync --frozen --no-dev --package csv-ingest-worker

ENV PATH="/app/.venv/bin:$PATH"

# Non-root (the worker writes no local filesystem at runtime).
RUN useradd --system --no-create-home disworker
USER disworker

# Documentation only: the readiness server binds the Cloud-Run-injected $PORT
# (default 8080) when RUN_HEALTH_SERVER is on; the pull loop itself needs no port.
EXPOSE 8080

CMD ["python", "-m", "csv_ingest_worker.main"]
