"""The quarantine record models — the WRITE shape of the two live tables.

Hand-aligned to the **live** ``ithina_dis_db`` ``quarantine.quarantined_chunks`` /
``quarantined_rows`` columns, deliberately restricted to the columns Slice 11a
WRITES: the lifecycle columns (``status``, ``resolution_note``, ``resolved_at``,
``resolved_by_user_id``) are NOT model fields — the slice writes ``status=NEW``
only, and the DB default stamps it, so a lifecycle transition cannot be expressed
through this model at all (transitions are a later, frontend-coordinated slice).
``id`` and ``last_updated_at`` are server-defaulted (``uuidv7()`` / ``now()``)
and likewise omitted from the INSERT.

Invariants baked in so a CHECK-violating row never reaches the INSERT:

- ``quarantined_at`` is timezone-aware UTC (``dis_core.timestamps.ensure_utc``).
- ``dis_channel`` mirrors the live ``ck_q*_dis_channel_vocab`` CHECK.
- ``failure_stage`` on a ROW record mirrors the live 6-member
  ``ck_qr_failure_stage_vocab`` subset (:data:`~dis_quarantine.failure_stages.ROW_FAILURE_STAGES`).
- ``failure_reason`` carries the stable :class:`~dis_audit.FailureCode` member —
  never an exception class name (the D79 vocabulary discipline); variable detail
  rides ``failure_context`` JSONB. ``failure_context`` never carries cell values
  (the dis-validation contract: column/check/reason grain only).
- ``trace_id`` / identity are caller-supplied off the envelope, never minted here
  (hard rule 4).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dis_core.timestamps import ensure_utc
from dis_quarantine.failure_stages import ROW_FAILURE_STAGES, QuarantineFailureStage

# The live ck_q*_dis_channel_vocab CHECK members (both tables).
_DIS_CHANNELS: frozenset[str] = frozenset({"csv_upload", "api", "csv_erp", "reverse_api"})


class _QuarantineRecordBase(BaseModel):
    """The columns the two tables share, at the write grain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # ---- Identity / correlation (the D78 seam) ----
    tenant_id: UUID
    store_id: UUID | None = None
    data_ingress_event_id: UUID
    trace_id: UUID
    source_id: str = Field(min_length=1, max_length=128)
    dis_channel: str = Field(max_length=32)
    gcs_uri: str = Field(min_length=1, max_length=1024)

    # ---- Failure context ----
    failure_stage: QuarantineFailureStage
    failure_reason: str = Field(min_length=1, max_length=256)
    failure_context: dict[str, Any] | None = None

    # ---- Lifecycle (write grain: the held moment only; status defaults NEW in the DB) ----
    quarantined_at: datetime

    @field_validator("dis_channel")
    @classmethod
    def _channel_in_vocab(cls, value: str) -> str:
        # Mirrors ck_q*_dis_channel_vocab so the violation surfaces at the model,
        # with context, not as an opaque CHECK failure at the INSERT.
        if value not in _DIS_CHANNELS:
            msg = f"dis_channel {value!r} is not in the live CHECK vocabulary {sorted(_DIS_CHANNELS)}"
            raise ValueError(msg)
        return value

    @field_validator("quarantined_at")
    @classmethod
    def _quarantined_at_must_be_utc(cls, value: datetime) -> datetime:
        # Raises NaiveDatetimeError for a naive datetime; normalises any aware zone to UTC.
        return ensure_utc(value)

    def _base_insert_params(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "store_id": self.store_id,
            "data_ingress_event_id": self.data_ingress_event_id,
            "trace_id": self.trace_id,
            "source_id": self.source_id,
            "dis_channel": self.dis_channel,
            "gcs_uri": self.gcs_uri,
            "failure_stage": self.failure_stage.value,
            "failure_reason": self.failure_reason,
            "failure_context": None if self.failure_context is None else json.dumps(self.failure_context),
            "quarantined_at": self.quarantined_at,
        }


class QuarantinedChunk(_QuarantineRecordBase):
    """One ``quarantine.quarantined_chunks`` row: a whole held ingress event."""

    # Known post-lookup only — NULL for pre-lookup failures (the mapping-config class).
    mapping_version_id: int | None = None
    row_count_in_chunk: int | None = Field(default=None, ge=0)

    def to_insert_params(self) -> dict[str, Any]:
        """Column -> value for the INSERT; server-defaulted columns omitted."""
        return {
            **self._base_insert_params(),
            "mapping_version_id": self.mapping_version_id,
            "row_count_in_chunk": self.row_count_in_chunk,
        }


class QuarantinedRow(_QuarantineRecordBase):
    """One ``quarantine.quarantined_rows`` row: one held data row of a chunk.

    The raw row payload is deliberately NOT carried (the live table's design):
    ``gcs_uri`` + ``row_offset`` locate it; ``failure_context`` carries the
    column/check/reason detail.
    """

    row_offset: int = Field(ge=0)
    row_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    # NOT NULL with an FK to config.source_mappings on the live table — row-grain
    # failures only exist post-lookup, so the version is always known.
    mapping_version_id: int

    @field_validator("failure_stage")
    @classmethod
    def _stage_in_row_vocab(cls, value: QuarantineFailureStage) -> QuarantineFailureStage:
        # Mirrors the live ck_qr_failure_stage_vocab SUBSET (rows cannot fail
        # pre-lookup stages); fails at the model, not the INSERT.
        if value not in ROW_FAILURE_STAGES:
            msg = (
                f"failure_stage {value!r} is not in the quarantined_rows CHECK subset "
                f"{sorted(s.value for s in ROW_FAILURE_STAGES)}"
            )
            raise ValueError(msg)
        return value

    def to_insert_params(self) -> dict[str, Any]:
        """Column -> value for the INSERT; server-defaulted columns omitted."""
        return {
            **self._base_insert_params(),
            "row_offset": self.row_offset,
            "row_sha256": self.row_sha256,
            "mapping_version_id": self.mapping_version_id,
        }
