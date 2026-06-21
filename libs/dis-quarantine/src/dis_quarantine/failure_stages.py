"""The ``failure_stage`` vocabulary the live quarantine CHECKs enforce.

The quarantine tables predate the audit ``Stage`` enum and use their own stage
vocabulary (``ck_qc_failure_stage_vocab`` / ``ck_qr_failure_stage_vocab`` on the
live tables — e.g. ``MAPPING_LOOKUP`` where audit says ``MAPPING_LOOKED_UP``).
This module owns that vocabulary and the total mapping from the audit ``Stage``
a failure was recorded at to the quarantine ``failure_stage`` it is held under,
so no caller hand-translates (and silently drifts).

The ROWS table's CHECK is a 6-member SUBSET of the chunks table's 9 (rows can
only fail post-lookup stages); :data:`ROW_FAILURE_STAGES` mirrors it so a
violating record fails model validation, never the INSERT.
"""

from __future__ import annotations

from enum import StrEnum

from dis_audit import Stage


class QuarantineFailureStage(StrEnum):
    """``failure_stage`` members — exactly the live ``ck_qc_failure_stage_vocab``."""

    PRE_INGEST_PII = "PRE_INGEST_PII"
    BRONZE_WRITE = "BRONZE_WRITE"
    MAPPING_LOOKUP = "MAPPING_LOOKUP"
    IDENTITY_VALIDATION = "IDENTITY_VALIDATION"
    PRE_MAPPING_VALIDATION = "PRE_MAPPING_VALIDATION"
    MAPPING_EXECUTION = "MAPPING_EXECUTION"
    POST_MAPPING_VALIDATION = "POST_MAPPING_VALIDATION"
    CANONICAL_WRITE = "CANONICAL_WRITE"
    OTHER = "OTHER"


# The live ck_qr_failure_stage_vocab subset (quarantined_rows).
ROW_FAILURE_STAGES: frozenset[QuarantineFailureStage] = frozenset(
    {
        QuarantineFailureStage.PRE_MAPPING_VALIDATION,
        QuarantineFailureStage.MAPPING_EXECUTION,
        QuarantineFailureStage.POST_MAPPING_VALIDATION,
        QuarantineFailureStage.IDENTITY_VALIDATION,
        QuarantineFailureStage.CANONICAL_WRITE,
        QuarantineFailureStage.OTHER,
    }
)


# Total over the audit Stage enum (unit-pinned), so failure_stage_for never KeyErrors
# on a stage a future caller passes. Stages with no quarantine sibling fall to OTHER
# (the vocabulary's own catch-member) — never invented members, never a silent skip.
_STAGE_TO_FAILURE_STAGE: dict[Stage, QuarantineFailureStage] = {
    Stage.RECEIVED: QuarantineFailureStage.OTHER,
    Stage.PII_TOKENIZED: QuarantineFailureStage.PRE_INGEST_PII,
    Stage.BRONZE_WRITTEN: QuarantineFailureStage.BRONZE_WRITE,
    Stage.INGRESS_PUBLISHED: QuarantineFailureStage.OTHER,
    Stage.MAPPING_LOOKED_UP: QuarantineFailureStage.MAPPING_LOOKUP,
    Stage.IDENTITY_VALIDATED: QuarantineFailureStage.IDENTITY_VALIDATION,
    Stage.PRE_MAPPING_VALIDATED: QuarantineFailureStage.PRE_MAPPING_VALIDATION,
    Stage.MAPPING_EXECUTED: QuarantineFailureStage.MAPPING_EXECUTION,
    Stage.POST_MAPPING_VALIDATED: QuarantineFailureStage.POST_MAPPING_VALIDATION,
    Stage.CANONICAL_WRITTEN: QuarantineFailureStage.CANONICAL_WRITE,
    Stage.QUARANTINED: QuarantineFailureStage.OTHER,
    Stage.SIGNAL_COMPUTED: QuarantineFailureStage.OTHER,
}


def failure_stage_for(stage: Stage) -> QuarantineFailureStage:
    """The quarantine ``failure_stage`` for the audit ``Stage`` a failure died in."""
    return _STAGE_TO_FAILURE_STAGE[stage]
