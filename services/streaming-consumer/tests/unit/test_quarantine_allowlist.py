"""The 11a allowlist guard truth table — the mutation pin for `_quarantinable`.

The integration suite proves the BEHAVIOR (held+acked / nacked+redelivered), but
two of those outcomes are doubly defended: the store-miss CONTRACT_VIOLATION
would nack even WITHOUT its carve-out, because the quarantine INSERT's unknown
``store_id`` violates ``fk_qc_store`` and the fail-loud posture falls back to
nack. That accidental rescue means a behavioral test alone cannot catch a
deleted carve-out — this truth table pins the GUARD itself, so a self-heal case
never even attempts a doomed hold (no FK noise, no reliance on the rescue).
"""

from __future__ import annotations

from dis_audit import FailureCode
from dis_core.errors import EventContractError, HotPositionMissingError, MappingConfigError
from streaming_consumer.orchestrate import _CHUNK_QUARANTINE_CODES, _FlowContext, _quarantinable

_POST_FETCH = _FlowContext(dis_channel="csv_upload", row_count=3)
_PRE_FETCH = _FlowContext()  # dis_channel unknown: the known-columns guard


def test_allowlist_membership_is_exactly_the_decided_set() -> None:
    # The narrow allowlist, pinned verbatim: widening it is a conscious decision
    # (a future slice), never a drive-by edit.
    assert _CHUNK_QUARANTINE_CODES == {
        FailureCode.MAPPING_CONFIG_INVALID,
        FailureCode.SUITE_REF_UNSUPPORTED,
        FailureCode.CONTRACT_VIOLATION,
    }


def test_deterministic_codes_are_quarantinable_post_fetch() -> None:
    exc = MappingConfigError("no ACTIVE mapping", tenant_id="t", trace_id="tr")
    assert _quarantinable(exc, FailureCode.MAPPING_CONFIG_INVALID, _POST_FETCH) is True
    assert _quarantinable(exc, FailureCode.SUITE_REF_UNSUPPORTED, _POST_FETCH) is True
    # A post-fetch contract violation that is NOT the store-miss (e.g. the
    # unparseable-CSV case carries no field) quarantines.
    csv_exc = EventContractError("bronze object is not parseable CSV")
    assert _quarantinable(csv_exc, FailureCode.CONTRACT_VIOLATION, _POST_FETCH) is True


def test_store_miss_contract_violation_is_carved_out() -> None:
    # The governing principle: mirror-sync lag heals it; retry is its designed
    # recovery. The guard must short-circuit BEFORE any hold attempt — the
    # behavioral nack alone is also produced by the fk_qc_store rescue, so this
    # pin is the only thing that catches a deleted carve-out.
    exc = EventContractError("store missing from mirror", field="store_id")
    assert _quarantinable(exc, FailureCode.CONTRACT_VIOLATION, _POST_FETCH) is False


def test_pre_fetch_failures_are_not_quarantinable() -> None:
    # The known-columns guard: dis_channel only exists post-fetch and the chunks
    # table's NOT NULL is unsatisfiable without it (the bronze-absent case).
    exc = EventContractError("bronze_ref has no bronze row", field="bronze_ref")
    assert _quarantinable(exc, FailureCode.CONTRACT_VIOLATION, _PRE_FETCH) is False
    cfg = MappingConfigError("no ACTIVE mapping")
    assert _quarantinable(cfg, FailureCode.MAPPING_CONFIG_INVALID, _PRE_FETCH) is False


def test_self_heal_and_transient_codes_keep_the_nack() -> None:
    # HOT_POSITION_MISSING self-heals via redelivery once the position onboards;
    # INFRA_FAILURE (and every unmapped code) is transient. Both nack.
    miss = HotPositionMissingError("no hot row")
    assert _quarantinable(miss, FailureCode.HOT_POSITION_MISSING, _POST_FETCH) is False
    assert _quarantinable(RuntimeError("boom"), FailureCode.INFRA_FAILURE, _POST_FETCH) is False
