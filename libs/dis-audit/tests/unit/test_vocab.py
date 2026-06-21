"""The owned vocabulary: closed Stage enum + scope/outcome mirroring the live CHECKs.

The live-CHECK *equality* (the real drift guard for EventScope/Outcome) is asserted in
the integration test against ``pg_constraint``; here we pin the owned membership so a
casual edit to the enum is caught without a DB.
"""

from __future__ import annotations

from enum import StrEnum

from dis_audit.stages import EventScope, Outcome, Stage

# Phase-1 pipeline stage set (events.sql header / BQ audit_events stage vocab). Phase-3-only
# BQ_EXPORTED / PARTITION_DROPPED are deliberately excluded (dead Phase-3 surface).
_PHASE1_STAGES = {
    "RECEIVED",
    "PII_TOKENIZED",
    "BRONZE_WRITTEN",
    "INGRESS_PUBLISHED",
    "MAPPING_LOOKED_UP",
    "IDENTITY_VALIDATED",
    "PRE_MAPPING_VALIDATED",
    "MAPPING_EXECUTED",
    "POST_MAPPING_VALIDATED",
    "CANONICAL_WRITTEN",
    "QUARANTINED",
    "SIGNAL_COMPUTED",
}


def test_vocab_are_string_enums() -> None:
    # StrEnum so a member is its own DB string value (Stage.CANONICAL_WRITTEN == "CANONICAL_WRITTEN").
    for enum_cls in (Stage, EventScope, Outcome):
        assert issubclass(enum_cls, StrEnum)
    assert Stage.CANONICAL_WRITTEN == "CANONICAL_WRITTEN"


def test_event_scope_membership() -> None:
    assert {s.value for s in EventScope} == {"INGRESS_EVENT", "ROW"}


def test_outcome_membership() -> None:
    # Exactly the six live CHECK values. FLIPPED by Slice 30c (the D42 revision):
    # DUPLICATE_NOOP / DUPLICATE_OVERWRITTEN are first-class members — promoted
    # from event_data for console queryability, superseding the Slice-10 JSONB
    # resolution. They refine SUCCESS (the append-only insert landed, D33).
    assert {o.value for o in Outcome} == {
        "SUCCESS",
        "FAILURE",
        "SKIPPED",
        "RETRIED",
        "DUPLICATE_NOOP",
        "DUPLICATE_OVERWRITTEN",
    }


def test_stage_is_closed_phase1_set() -> None:
    assert {s.value for s in Stage} == _PHASE1_STAGES
    # Phase-3-only stages stay out (phase boundary, not slice boundary).
    assert "BQ_EXPORTED" not in {s.value for s in Stage}
    assert "PARTITION_DROPPED" not in {s.value for s in Stage}
