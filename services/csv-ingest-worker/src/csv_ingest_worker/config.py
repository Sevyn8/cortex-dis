"""Environment-resolved configuration for the worker.

Required env (no silent default for a required value, code-quality rule 4 — a
missing one raises ``CsvIngestError``):

- ``POSTGRES_URL`` — the DIS write connection (``ithina_dis_user``). Reused by
  ``dis-rls`` ``create_rls_engine``, which positively asserts
  ``current_database()=='ithina_dis_db'`` (DIS on 5433, never Customer Master).
- ``PUBSUB_PROJECT_ID`` — the Pub/Sub project for the subscriber and publisher.
- ``GCS_BUCKET_BRONZE`` — the expected bronze bucket. The event's ``gcs_uri`` is
  split by ``dis-storage`` ``split_object_uri`` and the split-out bucket is
  cross-checked against this value (a mismatched bucket is a malformed producer).

The topic/subscription names are frozen-contract constants, not deployment config:
``csv.received`` is the trigger (D54), ``ingress.ready`` the publish target (hard
rule 10), and the subscription is provisioned by ``tools/local/create_topics.py``
(`make topics-create`) — NEVER by worker runtime code, so an absent subscription
is a loud startup error, not a silent auto-repair.

The dedup window is a decision value (24h, build-guide Slice 9b), not config.

Optional env (slice 40a, the toggled readiness-healthz wrapper):

- ``RUN_HEALTH_SERVER`` — ``"true"``/``"1"`` → run the /healthz HTTP server
  alongside the pull loop (Cloud Run Service mode). Unset/other → pure loop
  (local dev; future Worker Pools). A legitimately-optional boolean with a
  default-off, not a rule-4 silent fallback: absence is a valid configuration.
- ``PORT`` — the healthz server's port (Cloud Run injects it). REQUIRED — raises —
  only when ``RUN_HEALTH_SERVER`` is on; never read otherwise (no new required
  local env).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dis_core.errors import CsvIngestError
from dis_core.pubsub_names import resolve_pubsub_name

_POSTGRES_URL = "POSTGRES_URL"
_PUBSUB_PROJECT_ID = "PUBSUB_PROJECT_ID"
_GCS_BUCKET_BRONZE = "GCS_BUCKET_BRONZE"
_RUN_HEALTH_SERVER = "RUN_HEALTH_SERVER"
_PORT = "PORT"

SERVICE_NAME = "csv-ingest-worker"

# The worker SUBSCRIBES to csv.received (via CSV_RECEIVED_SUBSCRIPTION) and PUBLISHES
# ingress.ready. The contract names (hard rule 10) remain the defaults, so local dev
# (provisioned by tools/local/create_topics.py, no env set) is unchanged. Deployment
# overrides INGRESS_READY_TOPIC and CSV_RECEIVED_SUBSCRIPTION with the actually-
# provisioned short names (terraform sources them from the pubsub module output, so
# app and infra cannot drift). CSV_RECEIVED_TOPIC is NOT env-resolved: the worker does
# not publish it; this constant only labels the trigger in error/log strings.
CSV_RECEIVED_TOPIC = "csv.received"
INGRESS_READY_TOPIC = resolve_pubsub_name("INGRESS_READY_TOPIC", "ingress.ready")
CSV_RECEIVED_SUBSCRIPTION = resolve_pubsub_name("CSV_RECEIVED_SUBSCRIPTION", "csv-ingest-worker.csv.received")

# The idempotency window (decisions/build-guide: same content hash + upload session +
# tenant within 24h returns the prior trace_id). Measured against the prior bronze
# row's received_at — the only NOT NULL persisted timestamp, server-side and
# monotonic; the event's received_ts is producer-controlled and would skew under
# redelivery / late delivery.
DEDUP_WINDOW_HOURS = 24

# Readiness staleness threshold (slice 40a): /healthz reports stale past this many
# seconds since the loop's last heartbeat. Sized above the worst expected loop
# iteration (10s pull timeout + 1s error sleep + chunk-processing headroom) — a
# long pure-CPU stretch must not flap readiness; a dead loop must trip it.
HEALTH_STALENESS_SECONDS = 60.0


@dataclass(frozen=True)
class WorkerConfig:
    """Resolved environment profile for one worker process."""

    postgres_url: str
    pubsub_project_id: str
    bronze_bucket: str
    run_health_server: bool
    health_port: int | None

    @classmethod
    def from_env(cls) -> WorkerConfig:
        """Resolve from the environment, raising on any missing required value."""
        postgres_url = os.environ.get(_POSTGRES_URL)
        if not postgres_url:
            raise CsvIngestError(
                f"{_POSTGRES_URL} is not set; cannot reach the DIS database for the bronze write"
            )
        pubsub_project_id = os.environ.get(_PUBSUB_PROJECT_ID)
        if not pubsub_project_id:
            raise CsvIngestError(
                f"{_PUBSUB_PROJECT_ID} is not set; cannot subscribe to "
                f"{CSV_RECEIVED_TOPIC!r} or publish {INGRESS_READY_TOPIC!r}"
            )
        bronze_bucket = os.environ.get(_GCS_BUCKET_BRONZE)
        if not bronze_bucket:
            raise CsvIngestError(
                f"{_GCS_BUCKET_BRONZE} is not set; cannot cross-check the event's "
                "gcs_uri bucket or read the uploaded object"
            )
        run_health_server = os.environ.get(_RUN_HEALTH_SERVER, "").lower() in ("1", "true")
        health_port: int | None = None
        if run_health_server:
            port_raw = os.environ.get(_PORT)
            if not port_raw:
                raise CsvIngestError(
                    f"{_PORT} is not set but {_RUN_HEALTH_SERVER} is on; the healthz "
                    "server needs the Cloud-Run-injected port"
                )
            try:
                health_port = int(port_raw)
            except ValueError as exc:
                raise CsvIngestError(f"{_PORT}={port_raw!r} is not an integer port") from exc
        return cls(
            postgres_url=postgres_url,
            pubsub_project_id=pubsub_project_id,
            bronze_bucket=bronze_bucket,
            run_health_server=run_health_server,
            health_port=health_port,
        )
