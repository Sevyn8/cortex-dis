"""The auth dependency chain (slice Task 7): one valid path, every refusal mapped.

All requests go through the real app pipeline (probe routes mounted via the
test seam), so the asserted statuses and envelope codes are produced by the
registered exception handlers — not by unit-poking the dependencies.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _error_code(body: dict[str, Any]) -> str:
    code = body["error"]["code"]
    assert isinstance(code, str)
    return code


# -- the valid path -------------------------------------------------------------


def test_valid_tenant_token_yields_identity(client: TestClient, mint_token: Callable[..., str]) -> None:
    token = mint_token(sub="user-1", tenant_id=TENANT_A, roles=("dis:read",))
    response = client.get("/api/v1/probe/tenant", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json() == {"tenant_id": TENANT_A, "user_id": "user-1"}


def test_valid_ops_token_passes_require_ops(client: TestClient, mint_token: Callable[..., str]) -> None:
    # PLATFORM user: no tenant_id claim, dis:ops role (contract §2.1/§2.2).
    token = mint_token(sub="ops-1", tenant_id=None, roles=("dis:ops",), user_type="PLATFORM")
    response = client.get("/api/v1/probe/ops", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json() == {"user_id": "ops-1", "tenant_id": None}


# -- 401 AuthTokenError: missing / malformed / expired / bad parameters ----------


def test_missing_authorization_header_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/probe/tenant")
    assert response.status_code == 401
    assert _error_code(response.json()) == "auth_token"


def test_non_bearer_authorization_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/probe/tenant", headers={"Authorization": "Basic Zm9v"})
    assert response.status_code == 401
    assert _error_code(response.json()) == "auth_token"


def test_malformed_token_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/probe/tenant", headers=_bearer("not-a-jwt"))
    assert response.status_code == 401
    assert _error_code(response.json()) == "auth_token"


def test_expired_token_is_401(client: TestClient, mint_token: Callable[..., str]) -> None:
    token = mint_token(expires_in=-60)
    response = client.get("/api/v1/probe/tenant", headers=_bearer(token))
    assert response.status_code == 401
    assert _error_code(response.json()) == "auth_token"


def test_wrong_signature_is_401(client: TestClient, mint_token: Callable[..., str]) -> None:
    token = mint_token(secret="some-other-secret-long-enough-for-hs256-minimums")
    response = client.get("/api/v1/probe/tenant", headers=_bearer(token))
    assert response.status_code == 401
    assert _error_code(response.json()) == "auth_token"


def test_wrong_issuer_is_401(client: TestClient, mint_token: Callable[..., str]) -> None:
    token = mint_token(issuer="https://not-customer-master.local")
    response = client.get("/api/v1/probe/tenant", headers=_bearer(token))
    assert response.status_code == 401
    assert _error_code(response.json()) == "auth_token"


def test_wrong_audience_is_401(client: TestClient, mint_token: Callable[..., str]) -> None:
    token = mint_token(audience="not-dis")
    response = client.get("/api/v1/probe/tenant", headers=_bearer(token))
    assert response.status_code == 401
    assert _error_code(response.json()) == "auth_token"


def test_missing_sub_claim_is_401(client: TestClient, mint_token: Callable[..., str]) -> None:
    token = mint_token(omit=("sub",))
    response = client.get("/api/v1/probe/tenant", headers=_bearer(token))
    assert response.status_code == 401
    assert _error_code(response.json()) == "auth_token"


# -- 403 scope refusals -----------------------------------------------------------


def test_tenantless_token_on_tenant_endpoint_is_403(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    token = mint_token(tenant_id=None, roles=("dis:read",), user_type="PLATFORM")
    response = client.get("/api/v1/probe/tenant", headers=_bearer(token))
    assert response.status_code == 403
    assert _error_code(response.json()) == "tenant_scope"


def test_missing_ops_role_on_ops_endpoint_is_403(client: TestClient, mint_token: Callable[..., str]) -> None:
    token = mint_token(tenant_id=TENANT_A, roles=("dis:read", "dis:upload"))
    response = client.get("/api/v1/probe/ops", headers=_bearer(token))
    assert response.status_code == 403
    assert _error_code(response.json()) == "ops_role_required"


def test_absent_roles_claim_denies_ops(client: TestClient, mint_token: Callable[..., str]) -> None:
    # roles absent → no roles → deny-by-default at the ops gate.
    token = mint_token(tenant_id=None, roles=None, user_type="PLATFORM")
    response = client.get("/api/v1/probe/ops", headers=_bearer(token))
    assert response.status_code == 403
    assert _error_code(response.json()) == "ops_role_required"
