"""The fail-loud posture (no DB): the writer RAISES, never swallows (the dis-audit contrast)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from dis_core.errors import DisError, QuarantineWriteError
from dis_core.ids import new_uuid7
from dis_quarantine import PostgresQuarantineWriter, QuarantinedChunk, QuarantinedRow, QuarantineFailureStage

_TS = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)


class _ExplodingEngine:
    """A stand-in engine whose connect() fails — the store-down shape."""

    def connect(self) -> object:
        raise ConnectionError("store down")


def _chunk() -> QuarantinedChunk:
    return QuarantinedChunk(
        tenant_id=new_uuid7(),
        data_ingress_event_id=new_uuid7(),
        trace_id=new_uuid7(),
        source_id="sc_pos_v1",
        dis_channel="csv_upload",
        gcs_uri="gs://bronze/x.csv",
        failure_stage=QuarantineFailureStage.MAPPING_LOOKUP,
        failure_reason="MAPPING_CONFIG_INVALID",
        quarantined_at=_TS,
    )


def _row(tenant_id: UUID | None = None) -> QuarantinedRow:
    return QuarantinedRow(
        tenant_id=tenant_id or new_uuid7(),
        data_ingress_event_id=new_uuid7(),
        trace_id=new_uuid7(),
        source_id="sc_pos_v1",
        dis_channel="csv_upload",
        gcs_uri="gs://bronze/x.csv",
        row_offset=0,
        failure_stage=QuarantineFailureStage.POST_MAPPING_VALIDATION,
        failure_reason="VALIDATION_ROW_FAILED",
        mapping_version_id=1,
        quarantined_at=_TS,
    )


def test_quarantine_write_error_is_dis_error_rooted() -> None:
    assert issubclass(QuarantineWriteError, DisError)
    err = QuarantineWriteError("boom", tenant_id="t", trace_id="tr", failure_code="MAPPING_CONFIG_INVALID")
    assert (err.tenant_id, err.trace_id, err.failure_code) == ("t", "tr", "MAPPING_CONFIG_INVALID")


async def test_hold_chunk_raises_on_store_failure() -> None:
    # The OPPOSITE of dis-audit's fire-and-forget: a write failure RAISES (the
    # caller nacks; ack-and-lose is structurally impossible).
    writer = PostgresQuarantineWriter(_ExplodingEngine())  # type: ignore[arg-type]
    record = _chunk()
    with pytest.raises(QuarantineWriteError) as excinfo:
        await writer.hold_chunk(record)
    # Errors carry context (code-quality rule 5).
    assert excinfo.value.tenant_id == str(record.tenant_id)
    assert excinfo.value.trace_id == str(record.trace_id)
    assert excinfo.value.failure_code == "MAPPING_CONFIG_INVALID"


async def test_hold_rows_raises_on_store_failure() -> None:
    writer = PostgresQuarantineWriter(_ExplodingEngine())  # type: ignore[arg-type]
    with pytest.raises(QuarantineWriteError):
        await writer.hold_rows([_row()])


async def test_hold_rows_refuses_empty_and_mixed_tenants() -> None:
    # Caller bugs raise BEFORE any write attempt — never a silent no-op.
    writer = PostgresQuarantineWriter(_ExplodingEngine())  # type: ignore[arg-type]
    with pytest.raises(QuarantineWriteError, match="no records"):
        await writer.hold_rows([])
    with pytest.raises(QuarantineWriteError, match="tenants"):
        await writer.hold_rows([_row(), _row()])  # two distinct minted tenants
