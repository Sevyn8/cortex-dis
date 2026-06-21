"""AuditWriteError is DisError-rooted (AC8) and carries the load-bearing context."""

from __future__ import annotations

from dis_core.errors import AuditWriteError, DisError


def test_audit_write_error_is_dis_error_rooted() -> None:
    assert issubclass(AuditWriteError, DisError)


def test_audit_write_error_carries_context() -> None:
    err = AuditWriteError("boom", tenant_id="t", trace_id="tr", stage="CANONICAL_WRITTEN", failure_code="FK")
    assert err.tenant_id == "t"
    assert err.trace_id == "tr"
    assert err.stage == "CANONICAL_WRITTEN"
    assert err.failure_code == "FK"
