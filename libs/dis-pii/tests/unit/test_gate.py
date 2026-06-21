"""The fail-loud PII gate (AC4): raises when a flagged column has no backend, and the
not-raise branch is reachable only via an explicitly injected backend."""

from __future__ import annotations

import inspect

import pytest

from dis_core.errors import DisError, PiiBackendNotConfiguredError
from dis_pii.gate import assert_pii_handled

_PII_MAPPING = {"mapping_rules": {"rename": {"customer_email": "email"}}}
_BENIGN_MAPPING = {"mapping_rules": {"rename": {"qty": "units_sold"}}}


class _InjectedBackend:
    """A test-only injected placeholder backend; the gate only checks presence."""

    def tokenize(self, value: str, *, tenant_id: str) -> str:  # pragma: no cover - never called
        raise NotImplementedError


def test_raises_when_pii_flagged_and_no_backend() -> None:
    with pytest.raises(PiiBackendNotConfiguredError) as exc:
        assert_pii_handled(_PII_MAPPING, tenant_id="t-1", trace_id="trace-1")
    err = exc.value
    assert isinstance(err, DisError)  # rooted in the single DisError hierarchy
    assert err.tenant_id == "t-1"
    assert err.trace_id == "trace-1"
    # Carries the flagged column NAMES (sorted), never a raw PII value.
    assert err.columns == ("customer_email", "email")


def test_error_exposes_only_names_and_context_no_values() -> None:
    # Structural no-raw-PII property: the gate's only input is the mapping CONFIG
    # (column names), never row data, so no value can reach the error. Assert the error
    # carries column NAMES + caller context only, and that the message is count-based
    # (no column names, no values).
    mapping = {"mapping_rules": {"rename": {"customer_email": "email", "user_phone": "phone"}}}
    with pytest.raises(PiiBackendNotConfiguredError) as exc:
        assert_pii_handled(mapping, tenant_id="t-1", trace_id="tr-1")
    err = exc.value
    assert err.columns == ("customer_email", "email", "phone", "user_phone")  # names only
    assert err.tenant_id == "t-1"
    assert err.trace_id == "tr-1"
    # The message is minimal: a count, not the column names (and certainly no value).
    assert "4" in err.message
    for name in err.columns:
        assert name not in err.message


def test_injected_backend_reaches_not_raise_branch() -> None:
    # The ONLY way to not raise on a PII mapping: pass an explicit backend.
    assert_pii_handled(_PII_MAPPING, backend=_InjectedBackend())


def test_no_pii_mapping_does_not_raise() -> None:
    assert_pii_handled(_BENIGN_MAPPING)


def test_gate_has_no_flag_to_disable_it() -> None:
    # No config default / flag may disable the gate (hard rule 2, code-quality rule 4).
    params = set(inspect.signature(assert_pii_handled).parameters)
    forbidden = {"disable", "skip", "skip_gate", "enabled", "allow_pii", "bypass", "force"}
    assert params & forbidden == set(), f"gate exposes a disable-like flag: {params & forbidden}"
