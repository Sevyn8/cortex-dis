# dis-ui-server — the DIS UI backend-for-frontend (Slice 13a foundation).
#
# FIRST service Dockerfile in this repo (convention-setting). The service is a
# uv-workspace member, so the build context is the REPO ROOT and the workspace
# member directories are copied explicitly (uv refuses a workspace whose
# declared members are absent):
#
#   docker build -f services/dis-ui-server/Dockerfile -t dis-ui-server .
#
# Run (the container needs POSTGRES_URL; /healthz answers without a DB):
#
#   docker run --rm -p 8080:8080 \
#     -e POSTGRES_URL=postgresql+psycopg://ithina_dis_user:...@host:5433/ithina_dis_db \
#     dis-ui-server

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
RUN uv sync --frozen --no-dev --package dis-ui-server

ENV PATH="/app/.venv/bin:$PATH"

# Non-root (the service needs no filesystem writes at runtime).
RUN useradd --system --no-create-home disui
USER disui

EXPOSE 8080

CMD ["uvicorn", "dis_ui_server.main:app", "--host", "0.0.0.0", "--port", "8080"]
