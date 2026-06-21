"""The ``ingress.ready`` envelope (frozen contract, hard rule 10) and the publisher seam.

The envelope model is field-for-field the committed
``contracts/pubsub/ingress.ready.schema.json``; the unit drift guard reconciles both
directions. The worker POPULATES the contract, never changes its shape: identity is
the event's UUIDs, the external codes ride as the optional fields (producer-required
when present, D52 — the worker propagates them verbatim and never fabricates one),
``bronze_ref`` is the bronze row id, and ``received_ts`` is when DIS durably
accepted the chunk (the bronze row's ``received_at``) — deliberately DISTINCT from
the producer's ``csv.received.received_ts`` (see the service README / D59 note).

``Publisher`` is the seam tests inject against (``dis-testing``'s
``InMemoryPublisher`` satisfies it structurally; production code does not import
dis-testing). ``PubsubPublisher`` is the runtime implementation,
emulator-or-ambient (slice 40a): the emulator when ``PUBSUB_EMULATOR_HOST`` is set
(the ``pubsub_v1`` client honours it natively), real Pub/Sub via ambient
service-account credentials when it is not.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from csv_ingest_worker.config import SERVICE_NAME
from csv_ingest_worker.envelope import CsvReceivedEvent
from dis_core.logging import get_logger
from dis_core.timestamps import ensure_utc

_log = get_logger(SERVICE_NAME)


class Publisher(Protocol):
    """Publish ``data`` to ``topic_name``; return the message id."""

    def publish(self, topic_name: str, data: bytes) -> str: ...


class IngressReadyEnvelope(BaseModel):
    """One ``ingress.ready`` message — field-for-field the frozen contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trace_id: UUID
    tenant_id: UUID
    store_id: UUID
    source_id: str = Field(min_length=1)
    template_id: UUID
    bronze_ref: UUID
    gcs_uri: str = Field(min_length=1)
    received_ts: datetime
    # The delimiter the worker's preflight detected (Slice 16f); the consumer parses
    # with it. Single-char, default "," (backward-compat for any reader of a pre-16f
    # message). The worker POPULATES it on every publish path (fresh + resume).
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    tenant_display_code: str | None = None
    store_code: str | None = None
    replay: bool = False
    parent_trace_id: UUID | None = None

    def to_bytes(self) -> bytes:
        """Serialise for the wire; optional fields the worker cannot populate are
        OMITTED (never null-filled — the contract types them as string/uuid)."""
        payload = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload).encode()


def build_ingress_ready(
    event: CsvReceivedEvent,
    *,
    trace_id: UUID,
    bronze_ref: UUID,
    received_at: datetime,
    delimiter: str,
) -> IngressReadyEnvelope:
    """Populate the frozen envelope from the event + the landed bronze row.

    ``trace_id`` is passed explicitly (not read from ``event``) because the
    resume-and-mark path (D59) publishes under the PRIOR ingest's ``trace_id``;
    the fresh path passes the event's. Either way it is a READ trace_id — the
    worker mints none (hard rule 4, D54).

    ``template_id`` always comes off the INCOMING event (contract-required since
    Slice 8), never the bronze row — deliberately, so the resume-and-mark path
    cannot wedge on a pre-Slice-8 bronze row whose ``template_id`` column is NULL
    (the publish needs no bronze read for it).

    ``delimiter`` is the separator the worker's preflight sniff detected (Slice
    16f); it is passed explicitly because BOTH publish paths supply it — the fresh
    path from this run's preflight, the resume path from a re-derive over the same
    bytes (D59). The worker is the single detector; it never reads a delimiter off
    the incoming ``csv.received`` event.
    """
    return IngressReadyEnvelope(
        trace_id=trace_id,
        tenant_id=event.tenant_id,
        store_id=event.store_id,
        source_id=event.source_id,
        template_id=event.template_id,
        bronze_ref=bronze_ref,
        gcs_uri=event.gcs_uri,
        received_ts=ensure_utc(received_at),
        delimiter=delimiter,
        tenant_display_code=event.tenant_display_code,
        store_code=event.store_code,
        replay=False,
        parent_trace_id=None,
    )


class PubsubPublisher:
    """Runtime publisher, emulator-or-ambient (the dis-storage pattern, slice 40a).

    ``pubsub_v1.PublisherClient`` honours ``PUBSUB_EMULATOR_HOST`` natively: set →
    the emulator (local, unchanged); unset → real Pub/Sub via ambient
    application-default credentials. Construction is identical either way.
    """

    def __init__(self, *, project_id: str) -> None:
        mode = "emulator" if os.environ.get("PUBSUB_EMULATOR_HOST") else "ambient"
        _log.bind(stage="startup").info("pubsub publisher constructed", extra={"pubsub_mode": mode})
        from google.cloud import pubsub_v1  # lazy: only the runtime path needs it

        self._project_id = project_id
        self._client = pubsub_v1.PublisherClient()

    def publish(self, topic_name: str, data: bytes) -> str:
        topic_path = self._client.topic_path(self._project_id, topic_name)
        future = self._client.publish(topic_path, data)
        message_id: str = future.result()
        return message_id
