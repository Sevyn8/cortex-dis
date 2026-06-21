"""failure_stage vocabulary pins: CHECK mirrors + the total audit-Stage mapping."""

from __future__ import annotations

from dis_audit import Stage
from dis_quarantine.failure_stages import (
    ROW_FAILURE_STAGES,
    QuarantineFailureStage,
    failure_stage_for,
)

# The live ck_qc_failure_stage_vocab members, pinned verbatim (a DDL/vocab edit
# must consciously update this test).
_CHUNK_VOCAB = {
    "PRE_INGEST_PII",
    "BRONZE_WRITE",
    "MAPPING_LOOKUP",
    "IDENTITY_VALIDATION",
    "PRE_MAPPING_VALIDATION",
    "MAPPING_EXECUTION",
    "POST_MAPPING_VALIDATION",
    "CANONICAL_WRITE",
    "OTHER",
}

# The live ck_qr_failure_stage_vocab members (the rows SUBSET).
_ROW_VOCAB = {
    "PRE_MAPPING_VALIDATION",
    "MAPPING_EXECUTION",
    "POST_MAPPING_VALIDATION",
    "IDENTITY_VALIDATION",
    "CANONICAL_WRITE",
    "OTHER",
}


def test_enum_matches_live_chunk_check_vocab() -> None:
    assert {member.value for member in QuarantineFailureStage} == _CHUNK_VOCAB


def test_row_subset_matches_live_rows_check_vocab() -> None:
    assert {member.value for member in ROW_FAILURE_STAGES} == _ROW_VOCAB
    assert ROW_FAILURE_STAGES < set(QuarantineFailureStage)


def test_mapping_is_total_over_stage() -> None:
    # Every audit Stage maps — a new Stage member without a quarantine sibling
    # must consciously land here (OTHER is the explicit fallthrough, never a KeyError).
    for stage in Stage:
        assert failure_stage_for(stage) in QuarantineFailureStage


def test_consumer_stage_mappings_pinned() -> None:
    # The stage translations the 11a consumer wiring relies on.
    assert failure_stage_for(Stage.MAPPING_LOOKED_UP) is QuarantineFailureStage.MAPPING_LOOKUP
    assert failure_stage_for(Stage.PRE_MAPPING_VALIDATED) is QuarantineFailureStage.PRE_MAPPING_VALIDATION
    assert failure_stage_for(Stage.MAPPING_EXECUTED) is QuarantineFailureStage.MAPPING_EXECUTION
    assert failure_stage_for(Stage.POST_MAPPING_VALIDATED) is QuarantineFailureStage.POST_MAPPING_VALIDATION
    assert failure_stage_for(Stage.CANONICAL_WRITTEN) is QuarantineFailureStage.CANONICAL_WRITE
    assert failure_stage_for(Stage.RECEIVED) is QuarantineFailureStage.OTHER
