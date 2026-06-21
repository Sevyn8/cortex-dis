"""The inbound ``csv.received`` envelope, typed against the frozen contract.

One Pydantic model per the committed ``contracts/pubsub/csv.received.schema.json``
(hard rule 10: read and populate, never change shape). The unit drift guard
reconciles this model's field set against the contract file both directions, so a
contract edit and this model cannot silently diverge.

A contract violation (missing/empty/malformed required field — including the
idempotency-key component ``upload_session_id``) raises ``EventContractError``
(code-quality rule 4: required values never fall back silently). Terminal for the
message: a redelivery of the same malformed envelope fails identically.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dis_core.errors import EventContractError
from dis_core.timestamps import ensure_utc

# Mirrors the contract's upload_session_id pattern (the source_payload_id idempotency
# component, D54) — pinned here so a bad session id fails at the envelope boundary.
_UPLOAD_SESSION_PATTERN = r"^us_[a-z0-9]{12}$"


class CsvReceivedEvent(BaseModel):
    """One ``csv.received`` message — field-for-field the frozen contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    trace_id: UUID
    tenant_id: UUID
    store_id: UUID
    source_id: str = Field(min_length=1)
    # The mapping template the CSV was uploaded against (Slice 8, D71). Carried:
    # passed through to ingress.ready and persisted to bronze for replay lineage.
    # The worker never resolves or validates it (dis-ui-server validated ACTIVE
    # at Phase 1); the streaming consumer ignores it until Slice 8a.
    template_id: UUID
    upload_session_id: str = Field(pattern=_UPLOAD_SESSION_PATTERN)
    gcs_uri: str = Field(min_length=1)
    received_ts: datetime
    # Additive/optional (Slice 16f), single-char default ",". Carried for contract
    # symmetry with ingress.ready, but dis-ui-server does NOT populate it: the worker
    # detects the delimiter fresh in preflight (Approach B, the single detector) and
    # NEVER reads this field. Present here only so the drift guard (model_fields ==
    # contract properties) stays green and a future dis-ui change is non-breaking.
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    # Optional in the schema, producer-required when publishing (D52). The worker
    # propagates them verbatim onto ingress.ready; it never fabricates a code.
    tenant_display_code: str | None = None
    store_code: str | None = None

    @field_validator("received_ts")
    @classmethod
    def _received_ts_must_be_aware(cls, value: datetime) -> datetime:
        # DIS never handles naive datetimes; the contract format is ISO 8601 with zone.
        return ensure_utc(value)


def parse_csv_received(data: bytes) -> CsvReceivedEvent:
    """Parse one Pub/Sub message body into the typed envelope, or raise loudly.

    The raised ``EventContractError`` carries the violating field name(s) and the
    tenant/trace identifiers where the malformed payload still exposes them —
    identifiers only, never the payload body.
    """
    try:
        raw = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EventContractError(
            f"csv.received message body is not valid JSON: {exc}",
        ) from exc
    if not isinstance(raw, dict):
        raise EventContractError(
            "csv.received message body is not a JSON object",
        )
    try:
        return CsvReceivedEvent.model_validate(raw)
    except ValidationError as exc:
        fields = sorted({str(err["loc"][0]) for err in exc.errors() if err["loc"]})
        raise EventContractError(
            f"csv.received envelope violates the frozen contract; bad field(s): "
            f"{', '.join(fields) or '<root>'}",
            field=fields[0] if fields else None,
            tenant_id=_identifier_or_none(raw, "tenant_id"),
            trace_id=_identifier_or_none(raw, "trace_id"),
        ) from exc


def _identifier_or_none(raw: dict[str, object], key: str) -> str | None:
    """A string identifier from the raw payload for error context, if present."""
    value = raw.get(key)
    return value if isinstance(value, str) else None
