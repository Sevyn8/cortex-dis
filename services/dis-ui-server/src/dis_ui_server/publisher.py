"""The ``csv.received`` envelope (frozen contract, hard rule 10) and the publisher seam.

The envelope model is field-for-field the committed
``contracts/pubsub/csv.received.schema.json``; the unit drift guard reconciles both
directions. This service POPULATES the contract, never changes its shape: identity
is the resolved internal UUIDs (D37/D52), the external codes ride as the optional
fields (producer-required when present, D52), ``template_id`` is the validated
ACTIVE template (Slice 8 / D71 carry), and ``upload_session_id`` is the
deterministic per-upload lineage id (the worker's D58 idempotency component —
see ``handlers/csv_uploads.py`` ``derive_upload_session_id``).

``Publisher`` is the seam tests inject against; ``PubsubPublisher`` is the runtime
implementation, emulator-or-ambient exactly like ``dis-storage`` (slice 40a): the
emulator when ``PUBSUB_EMULATOR_HOST`` is set (the ``pubsub_v1`` client honours the
env var natively), real Pub/Sub via ambient service-account credentials when it is
not.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dis_core.logging import get_logger
from dis_core.timestamps import ensure_utc

_log = get_logger("dis-ui-server")


class Publisher(Protocol):
    """Publish ``data`` to ``topic_name``; return the message id."""

    def publish(self, topic_name: str, data: bytes) -> str: ...


class CsvReceivedEnvelope(BaseModel):
    """One ``csv.received`` message — field-for-field the frozen contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trace_id: UUID
    tenant_id: UUID
    store_id: UUID
    source_id: str = Field(min_length=1)
    template_id: UUID
    upload_session_id: str = Field(pattern=r"^us_[a-z0-9]{12}$")
    gcs_uri: str = Field(min_length=1)
    received_ts: datetime
    tenant_display_code: str | None = None
    store_code: str | None = None
    delimiter: str = Field(default=",", min_length=1, max_length=1)

    def to_bytes(self) -> bytes:
        """Serialise for the wire; optional fields without a value are OMITTED
        (never null-filled — the contract types them as string)."""
        payload = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload).encode()


def build_csv_received(
    *,
    trace_id: UUID,
    tenant_id: UUID,
    store_id: UUID,
    source_id: str,
    template_id: UUID,
    upload_session_id: str,
    gcs_uri: str,
    received_ts: datetime,
    tenant_display_code: str | None,
    store_code: str | None,
) -> CsvReceivedEnvelope:
    """Populate the frozen envelope from the resolved upload.

    Every identity value arrives RESOLVED (tenant from the verified token, store
    from the mirror, source from the template lineage) — this builder only
    assembles; it resolves nothing and mints nothing.
    """
    return CsvReceivedEnvelope(
        trace_id=trace_id,
        tenant_id=tenant_id,
        store_id=store_id,
        source_id=source_id,
        template_id=template_id,
        upload_session_id=upload_session_id,
        gcs_uri=gcs_uri,
        received_ts=ensure_utc(received_ts),
        tenant_display_code=tenant_display_code,
        store_code=store_code,
    )


class PubsubPublisher:
    """Runtime publisher, emulator-or-ambient (the dis-storage pattern, slice 40a).

    ``pubsub_v1.PublisherClient`` honours ``PUBSUB_EMULATOR_HOST`` natively: set →
    the emulator (local, unchanged); unset → real Pub/Sub via ambient
    application-default credentials. Construction is identical either way and
    offline — the gRPC channel connects on first publish — so app startup stays
    lazy, matching the engine posture.
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
