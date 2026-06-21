"""DisError leaves render the §2.3 envelope with the correct status (Task 5).

Every assertion goes through a probe route that RAISES — the response below is
produced by the registered exception handlers, end to end.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"


def _error(body: dict[str, Any]) -> dict[str, Any]:
    error = body["error"]
    assert isinstance(error, dict)
    # The full §2.3 envelope shape, every time.
    assert set(error.keys()) == {"code", "message", "trace_id", "details"}
    return error


def test_auth_token_error_renders_401(client: TestClient) -> None:
    response = client.get("/api/v1/probe/raise/auth-token")
    assert response.status_code == 401
    error = _error(response.json())
    assert error["code"] == "auth_token"
    assert error["message"] == "probe auth failure"
    assert error["details"] == {"reason": "probe"}


def test_tenant_scope_error_renders_403_with_context(client: TestClient) -> None:
    response = client.get("/api/v1/probe/raise/tenant-scope")
    assert response.status_code == 403
    error = _error(response.json())
    assert error["code"] == "tenant_scope"
    # Load-bearing identifiers ride details (code-quality rule 5).
    assert error["details"] == {"tenant_id": TENANT_A}


def test_ops_role_required_error_renders_403(client: TestClient) -> None:
    response = client.get("/api/v1/probe/raise/ops-role-required")
    assert response.status_code == 403
    assert _error(response.json())["code"] == "ops_role_required"


def test_rls_context_error_renders_500(client: TestClient) -> None:
    response = client.get("/api/v1/probe/raise/rls-context")
    assert response.status_code == 500
    error = _error(response.json())
    assert error["code"] == "rls_context"
    assert error["details"] == {"database": "wrong_db", "role": "some_role"}


def test_unmapped_dis_error_falls_back_to_500(client: TestClient) -> None:
    # A real DisError leaf with no explicit status mapping: visible 500, same
    # envelope — never a bare traceback, never an invented status.
    response = client.get("/api/v1/probe/raise/unmapped")
    assert response.status_code == 500
    error = _error(response.json())
    assert error["code"] == "mirror_sync"
    assert error["details"] == {"tenant_id": TENANT_A}


def test_trace_id_is_null_when_none_is_bound(client: TestClient) -> None:
    # 13a mints no trace_id (minting is the two ingress-starting endpoints,
    # later slices) — the field is present and null, not absent.
    response = client.get("/api/v1/probe/raise/auth-token")
    assert response.json()["error"]["trace_id"] is None


def test_trace_id_is_carried_when_bound(client: TestClient) -> None:
    response = client.get("/api/v1/probe/raise/traced")
    trace_id = response.json()["error"]["trace_id"]
    assert isinstance(trace_id, str) and len(trace_id) == 36  # canonical UUID form


def test_non_dis_exception_leaks_no_internals(lenient_client: TestClient) -> None:
    # A bug raising a NON-DisError must not echo its message, type, or a
    # traceback over the wire: Starlette's generic 500 body only.
    response = lenient_client.get("/api/v1/probe/raise/non-dis")
    assert response.status_code == 500
    assert "sentinel-internal-context-do-not-leak" not in response.text
    assert "ValueError" not in response.text
    assert "Traceback" not in response.text


def test_validation_failure_uses_the_envelope_and_strips_values(
    client: TestClient,
) -> None:
    sentinel = "definitely-not-an-int-PII-ish-value"
    response = client.post("/api/v1/probe/validated", json={"value": sentinel})
    assert response.status_code == 422
    error = _error(response.json())
    assert error["code"] == "request_validation"
    # Locations and messages survive; the submitted VALUE must not echo back.
    assert sentinel not in response.text
    locs = [err["loc"] for err in error["details"]["errors"]]
    assert ["body", "value"] in locs
