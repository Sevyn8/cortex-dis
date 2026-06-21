"""Quarantine record invariants (no DB): vocab mirrors, UTC enforcement, INSERT shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from dis_core.ids import new_uuid7
from dis_core.timestamps import NaiveDatetimeError
from dis_quarantine.failure_stages import QuarantineFailureStage
from dis_quarantine.records import QuarantinedChunk, QuarantinedRow

_TS = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)


def _chunk(**overrides: object) -> QuarantinedChunk:
    base: dict[str, object] = {
        "tenant_id": new_uuid7(),
        "store_id": new_uuid7(),
        "data_ingress_event_id": new_uuid7(),
        "trace_id": new_uuid7(),
        "source_id": "sc_pos_v1",
        "dis_channel": "csv_upload",
        "gcs_uri": "gs://bronze/tenant/x/object.csv",
        "failure_stage": QuarantineFailureStage.MAPPING_LOOKUP,
        "failure_reason": "MAPPING_CONFIG_INVALID",
        "quarantined_at": _TS,
    }
    base.update(overrides)
    return QuarantinedChunk(**base)


def _row(**overrides: object) -> QuarantinedRow:
    base: dict[str, object] = {
        "tenant_id": new_uuid7(),
        "store_id": new_uuid7(),
        "data_ingress_event_id": new_uuid7(),
        "trace_id": new_uuid7(),
        "source_id": "sc_pos_v1",
        "dis_channel": "csv_upload",
        "gcs_uri": "gs://bronze/tenant/x/object.csv",
        "row_offset": 3,
        "failure_stage": QuarantineFailureStage.POST_MAPPING_VALIDATION,
        "failure_reason": "VALIDATION_ROW_FAILED",
        "mapping_version_id": 42,
        "quarantined_at": _TS,
    }
    base.update(overrides)
    return QuarantinedRow(**base)


def test_chunk_insert_params_omit_server_defaulted_columns() -> None:
    params = _chunk(failure_context={"failure_message": "no ACTIVE mapping"}).to_insert_params()
    # id / status / last_updated_at are DB-stamped (uuidv7() / 'NEW' / now()); the
    # lifecycle columns are not even model fields (status=NEW-only write grain).
    for absent in ("id", "status", "last_updated_at", "resolution_note", "resolved_at"):
        assert absent not in params
    assert len(params) == 13
    # failure_context is serialised to a JSON string (cast to JSONB in SQL).
    assert isinstance(params["failure_context"], str)
    # The enum is sent as its string value (the live CHECK vocabulary).
    assert params["failure_stage"] == "MAPPING_LOOKUP"


def test_row_insert_params_shape() -> None:
    params = _row().to_insert_params()
    for absent in ("id", "status", "last_updated_at"):
        assert absent not in params
    assert len(params) == 14
    assert params["row_offset"] == 3
    assert params["mapping_version_id"] == 42
    assert params["row_sha256"] is None  # no row hash exists at gate time (by design)


def test_quarantined_at_normalised_to_utc_and_naive_rejected() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    chunk = _chunk(quarantined_at=datetime(2026, 6, 6, 17, 30, tzinfo=ist))
    assert chunk.quarantined_at == _TS
    with pytest.raises((NaiveDatetimeError, ValidationError)):
        _chunk(quarantined_at=datetime(2026, 6, 6, 12, 0))  # naive


def test_dis_channel_vocab_mirrored() -> None:
    # ck_q*_dis_channel_vocab: the violation surfaces at the model, not the INSERT.
    with pytest.raises(ValidationError, match="dis_channel"):
        _chunk(dis_channel="sftp")


def test_row_failure_stage_subset_mirrored() -> None:
    # ck_qr_failure_stage_vocab is a SUBSET: a pre-lookup stage on a ROW record fails.
    with pytest.raises(ValidationError, match="failure_stage"):
        _row(failure_stage=QuarantineFailureStage.MAPPING_LOOKUP)
    # The same stage is fine on a CHUNK record (the 9-member chunks vocabulary).
    assert _chunk(failure_stage=QuarantineFailureStage.MAPPING_LOOKUP)


def test_row_offset_non_negative_and_mapping_version_required() -> None:
    with pytest.raises(ValidationError):
        _row(row_offset=-1)  # ck_qr_row_offset_non_negative mirrored
    with pytest.raises(ValidationError):
        QuarantinedRow(  # type: ignore[call-arg]  # mapping_version_id NOT NULL on rows
            tenant_id=new_uuid7(),
            data_ingress_event_id=new_uuid7(),
            trace_id=new_uuid7(),
            source_id="sc_pos_v1",
            dis_channel="csv_upload",
            gcs_uri="gs://bronze/x.csv",
            row_offset=0,
            failure_stage=QuarantineFailureStage.POST_MAPPING_VALIDATION,
            failure_reason="VALIDATION_ROW_FAILED",
            quarantined_at=_TS,
        )


def test_chunk_mapping_version_nullable_store_nullable() -> None:
    # Pre-lookup chunk failures carry no mapping_version_id; store_id is nullable.
    chunk = _chunk(mapping_version_id=None, store_id=None)
    params = chunk.to_insert_params()
    assert params["mapping_version_id"] is None
    assert params["store_id"] is None


def test_extra_fields_forbidden_and_records_frozen() -> None:
    with pytest.raises(ValidationError):
        _chunk(status="RESOLVED")  # lifecycle is not expressible at the write grain
    chunk = _chunk()
    with pytest.raises(ValidationError):
        chunk.failure_reason = "EDITED"  # type: ignore[misc]
