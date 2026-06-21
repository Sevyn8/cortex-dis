"""The inbound ``ingress.ready`` envelope, typed against the frozen contract.

One Pydantic model per the committed ``contracts/pubsub/ingress.ready.schema.json``
(hard rule 10: read and populate, never change shape). The unit drift guard
reconciles this model's field set against the contract file both directions, so a
contract edit and this model cannot silently diverge.

A contract violation (missing/empty/malformed required field) raises
``EventContractError`` (code-quality rule 4: required values never fall back
silently; the class lives under the ``CsvIngestError`` family in dis-core — reused
here rather than minting a sibling, since dis-core is outside this slice's blast
radius). Terminal for the message: a redelivery of the same malformed envelope
fails identically, so the subscriber acks it.

The consumer READS identity and ``trace_id`` off this event and mints neither
(hard rule 4, D54 trust model); ``ingress.resubmit`` (replay) is Slice 12 and is
not parsed here.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dis_core.errors import EventContractError
from dis_core.timestamps import ensure_utc


class IngressReadyEvent(BaseModel):
    """One ``ingress.ready`` message — field-for-field the frozen contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    trace_id: UUID
    tenant_id: UUID
    store_id: UUID
    source_id: str = Field(min_length=1)
    # CONSUMED since Slice 8a (D71 closed): the mapping template the payload was
    # uploaded against, required by the contract. It keys the active-mapping
    # lookup (pipeline/mapping.py), so the consumer applies the exact template's
    # rules — a second ACTIVE template per source is safe. Absent is structurally
    # unreachable downstream: a message without it fails THIS model's parse
    # (contract-reject, terminally acked) before any pipeline stage runs.
    template_id: UUID
    bronze_ref: UUID
    gcs_uri: str = Field(min_length=1)
    received_ts: datetime
    # The CSV field separator the worker detected in preflight (Slice 16f); passed to
    # pl.read_csv(separator=...) at fetch. Single-char, default "," — a pre-16f or
    # replayed message that lacks it parses as comma (backward-compatible), the same
    # behaviour as before this slice.
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    # Optional in the schema, producer-required when publishing (D52). Readability
    # only; never a substitute for the UUIDs.
    tenant_display_code: str | None = None
    store_code: str | None = None
    # Replay markers (Slice 12 consumes ingress.resubmit; fresh ingress arrives
    # with replay absent/false — the model still parses the contract faithfully).
    replay: bool = False
    parent_trace_id: UUID | None = None

    @field_validator("received_ts")
    @classmethod
    def _received_ts_must_be_aware(cls, value: datetime) -> datetime:
        # DIS never handles naive datetimes; the contract format is ISO 8601 with zone.
        return ensure_utc(value)


def parse_ingress_ready(data: bytes) -> IngressReadyEvent:
    """Parse one Pub/Sub message body into the typed envelope, or raise loudly.

    The raised ``EventContractError`` carries the violating field name(s) and the
    tenant/trace identifiers where the malformed payload still exposes them —
    identifiers only, never the payload body.
    """
    try:
        raw = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EventContractError(
            f"ingress.ready message body is not valid JSON: {exc}",
        ) from exc
    if not isinstance(raw, dict):
        raise EventContractError(
            "ingress.ready message body is not a JSON object",
        )
    try:
        return IngressReadyEvent.model_validate(raw)
    except ValidationError as exc:
        fields = sorted({str(err["loc"][0]) for err in exc.errors() if err["loc"]})
        raise EventContractError(
            f"ingress.ready envelope violates the frozen contract; bad field(s): "
            f"{', '.join(fields) or '<root>'}",
            field=fields[0] if fields else None,
            tenant_id=_identifier_or_none(raw, "tenant_id"),
            trace_id=_identifier_or_none(raw, "trace_id"),
        ) from exc


def _identifier_or_none(raw: dict[str, object], key: str) -> str | None:
    """A string identifier from the raw payload for error context, if present."""
    value = raw.get(key)
    return value if isinstance(value, str) else None
