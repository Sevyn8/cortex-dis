# mirror-sync-consumer
#
# Syncs Customer Master tenants/stores into identity_mirror (DB pull + the
# identity.changed consumer). NOT an HTTP server. Modeled on the uv-based
# pattern. CONFIRM the entrypoint against
# services/mirror-sync-consumer/src/mirror_sync_consumer/ (main/runner).
#
# Build from the REPO ROOT:
#   docker build -f docker/mirror-sync-consumer.Dockerfile -t IMAGE .

FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY services/mirror-sync-consumer ./services/mirror-sync-consumer

RUN uv sync --frozen --package mirror-sync-consumer --no-dev

# CONFIRM the real entrypoint (this service may have a runner rather than main).
CMD ["uv", "run", "--package", "mirror-sync-consumer", "python", "-m", "mirror_sync_consumer.main"]
