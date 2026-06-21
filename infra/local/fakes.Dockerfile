# Shared image for the Slice 2 fakes (Customer Master, Identity Service).
#
# Test infrastructure only — these images run the FastAPI fakes from
# libs/dis-testing. The compose `command` selects which fake to run.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install just the two libs the fakes need (dis-testing pulls dis-core + fastapi,
# uvicorn, pyjwt[crypto], google-cloud-pubsub, httpx, ...). No heavy root deps.
COPY libs/dis-core ./libs/dis-core
COPY libs/dis-testing ./libs/dis-testing
RUN uv pip install --system ./libs/dis-core ./libs/dis-testing

# Default port; overridden per service in docker-compose.
EXPOSE 8080
