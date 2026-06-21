"""The wired dis-pii gate fires on REAL heuristic detection, before any persistence.

The header names used here are ones dis-pii's actual matcher recognises (the
``email|phone|mobile|msisdn|loyalty|aadhaa?r`` pattern and the ``pan``/``ssn``
whole-token set in ``dis_pii.detectors``) — so these tests prove the gate genuinely
fires through the worker's wiring, not that a contrived shape happens to pass.
The synthetic-mapping equivalence test proves the wiring exposes header names to
detection exactly as a real ``mapping_rules.rename`` would (operator note 2).
"""

from __future__ import annotations

import pytest

from csv_ingest_worker.pii_gate import gate_csv_headers, synthetic_mapping
from dis_core.errors import DisError, PiiBackendNotConfiguredError
from dis_pii import detect_pii_columns

_TENANT = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees
_TRACE = "019e8d88-4e76-7911-bb77-d8fcba1808a6"

# Header names the REAL dis-pii heuristic detects (pattern + whole-token rules).
_PII_HEADERS = ["customer_email", "phone_number", "customer_pan", "loyalty_card_no"]
# Plausible retail headers the heuristic does NOT flag.
_CLEAN_HEADERS = ["sku", "store_section", "qty_sold", "unit_price", "company"]


class _RecordingBackend:
    """A placeholder PiiBackend for the injected-backend (not-raise) branch."""

    def tokenize(self, value: str, *, tenant_id: str) -> str:
        return f"tok::{tenant_id}::{value}"


@pytest.mark.parametrize("pii_header", _PII_HEADERS)
def test_recognised_pii_header_raises_fail_loud(pii_header: str) -> None:
    columns = [*_CLEAN_HEADERS, pii_header]
    with pytest.raises(PiiBackendNotConfiguredError) as exc_info:
        gate_csv_headers(columns, tenant_id=_TENANT, trace_id=_TRACE)
    err = exc_info.value
    assert pii_header in err.columns  # column NAMES only — never values
    assert err.tenant_id == _TENANT
    assert err.trace_id == _TRACE
    assert issubclass(PiiBackendNotConfiguredError, DisError)


def test_clean_headers_pass_with_zero_detected() -> None:
    detected = gate_csv_headers(_CLEAN_HEADERS, tenant_id=_TENANT, trace_id=_TRACE)
    assert detected == frozenset()


def test_whole_token_rule_does_not_false_positive() -> None:
    # 'company' must not trip the 'pan' token; 'japan_region' must not either.
    detected = gate_csv_headers(["company", "japan_region"], tenant_id=_TENANT, trace_id=_TRACE)
    assert detected == frozenset()


def test_not_raise_branch_reachable_only_via_injected_backend() -> None:
    # The ONLY way past a detected PII column is an explicitly injected backend
    # (tests); there is no config default that disables the gate (dis-pii invariant).
    detected = gate_csv_headers(
        _PII_HEADERS,
        tenant_id=_TENANT,
        trace_id=_TRACE,
        backend=_RecordingBackend(),
    )
    assert detected == frozenset(_PII_HEADERS)


def test_gate_has_no_env_or_config_off_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    # No environment variable may disable the gate; a hostile env still raises.
    monkeypatch.setenv("DIS_PII_DISABLE", "1")
    monkeypatch.setenv("PII_GATE_DISABLED", "true")
    with pytest.raises(PiiBackendNotConfiguredError):
        gate_csv_headers(["customer_email"], tenant_id=_TENANT, trace_id=_TRACE)


def test_synthetic_mapping_detects_exactly_like_a_real_mapping(  # operator note 2
) -> None:
    # The worker's synthetic {"rename": {h: h}} must expose header names to
    # dis-pii detection EXACTLY as a real config.source_mappings row would.
    headers = [*_CLEAN_HEADERS, "customer_email", "alt_phone"]
    real_mapping_row = {
        "tenant_id": _TENANT,
        "source_id": "manual_csv_upload",
        "mapping_rules": {
            "rename": {h: h for h in headers},
            "normalize": {},
            "cast": {},
            "derive": {},
        },
    }
    assert detect_pii_columns(synthetic_mapping(headers)) == detect_pii_columns(real_mapping_row)
    assert detect_pii_columns(synthetic_mapping(headers)) == frozenset({"customer_email", "alt_phone"})
