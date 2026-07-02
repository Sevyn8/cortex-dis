"""Atlas A5: require_super_admin resolves atlas:schema:publish from Customer Master.

Two layers, no network:

- GATE tests go through the real app pipeline (the ``/probe/super-admin`` route on
  ``Depends(require_super_admin)``), with a FAKE CM client injected via the seam
  (``set_permissions_client``) — mirroring how the verifier is faked. They assert
  the mapped HTTP status + envelope code the registered handlers produce: GRANT
  (200), no-grant (403), and the three FAIL-CLOSED paths (all deny, never grant).
- CLIENT tests drive the real ``HttpCmPermissionsClient`` over an ``httpx.MockTransport``
  double (still no network) to prove it reads the ``"permissions"`` envelope key and
  translates non-200 / timeout / malformed-body into the typed errors.
- MAPPING tests unit the pure ``roles_from_grants`` helper.

CM is authoritative: the token carries NO roles claim (as real CM tokens don't), so
a grant here can only come from the CM hop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from dis_core.errors import (
    CmPermissionsClientError,
    CmPermissionsUnavailableError,
)
from dis_ui_server.auth import cm_permissions as cm
from dis_ui_server.auth.cm_permissions import (
    HttpCmPermissionsClient,
    PermissionGrant,
    roles_from_grants,
)

_ATLAS_GRANT = PermissionGrant(
    module="ATLAS", resource="SCHEMA", action="PUBLISH", scope="GLOBAL", anchor_path=None
)
_UNRELATED_GRANT = PermissionGrant(
    module="PRICING_OS", resource="MARKDOWNS", action="APPROVE", scope="STORE", anchor_path="t.r.s"
)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _error_code(body: dict[str, Any]) -> str:
    code = body["error"]["code"]
    assert isinstance(code, str)
    return code


def _platform_token(mint_token: Callable[..., str]) -> str:
    # Super Admin is platform-scoped; real CM tokens carry NO roles claim.
    return mint_token(sub="sa-1", tenant_id=None, roles=None, user_type="PLATFORM")


# -- fake CM clients (honor the CmPermissionsClient Protocol) --------------------


class _FakeReturning:
    def __init__(self, grants: list[PermissionGrant]) -> None:
        self._grants = grants

    async def get_permissions(self, bearer_token: str) -> list[PermissionGrant]:
        assert bearer_token  # the raw token is forwarded, not empty
        return self._grants


class _FakeRaising:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def get_permissions(self, bearer_token: str) -> list[PermissionGrant]:
        raise self._exc


# -- GATE: grant + deny ----------------------------------------------------------


def test_super_admin_granted_when_cm_returns_atlas_grant(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    cm.set_permissions_client(_FakeReturning([_ATLAS_GRANT]))
    response = client.get("/api/v1/probe/super-admin", headers=_bearer(_platform_token(mint_token)))
    assert response.status_code == 200
    assert response.json() == {"user_id": "sa-1", "tenant_id": None}


def test_super_admin_denied_when_cm_lacks_grant(client: TestClient, mint_token: Callable[..., str]) -> None:
    # CM returns permissions, but not atlas:schema:publish -> 403 (not a super admin).
    cm.set_permissions_client(_FakeReturning([_UNRELATED_GRANT]))
    response = client.get("/api/v1/probe/super-admin", headers=_bearer(_platform_token(mint_token)))
    assert response.status_code == 403
    assert _error_code(response.json()) == "super_admin_required"


# -- GATE: the three FAIL-CLOSED paths (every one denies, never grants) ----------


def test_super_admin_fail_closed_when_cm_unavailable(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    cm.set_permissions_client(_FakeRaising(CmPermissionsUnavailableError("CM down")))
    response = client.get("/api/v1/probe/super-admin", headers=_bearer(_platform_token(mint_token)))
    assert response.status_code == 503
    assert _error_code(response.json()) == "cm_permissions_unavailable"


def test_super_admin_fail_closed_on_timeout(client: TestClient, mint_token: Callable[..., str]) -> None:
    # A raw httpx.TimeoutException (not a CmPermissions* error) must still deny:
    # the gate's broad backstop translates it to a 503, never a grant.
    cm.set_permissions_client(_FakeRaising(httpx.TimeoutException("read timeout")))
    response = client.get("/api/v1/probe/super-admin", headers=_bearer(_platform_token(mint_token)))
    assert response.status_code == 503
    assert _error_code(response.json()) == "cm_permissions_unavailable"


def test_super_admin_fail_closed_on_non_200(client: TestClient, mint_token: Callable[..., str]) -> None:
    cm.set_permissions_client(_FakeRaising(CmPermissionsClientError("CM 500", status_code=500)))
    response = client.get("/api/v1/probe/super-admin", headers=_bearer(_platform_token(mint_token)))
    assert response.status_code == 503
    assert _error_code(response.json()) == "cm_permissions_client"


def test_super_admin_missing_cm_base_url_fails_closed(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    # No client injected + CM_BASE_URL unset (unit env sets none): the lazy builder
    # fails closed rather than granting or crashing the whole service.
    cm.set_permissions_client(None)
    response = client.get("/api/v1/probe/super-admin", headers=_bearer(_platform_token(mint_token)))
    assert response.status_code == 503
    assert _error_code(response.json()) == "cm_permissions_unavailable"


# -- CLIENT: real HttpCmPermissionsClient over a MockTransport (no network) -------


def _client_with_transport(handler: Callable[[httpx.Request], httpx.Response]) -> HttpCmPermissionsClient:
    transport = httpx.MockTransport(handler)
    return HttpCmPermissionsClient(
        "https://cm.example", client=httpx.AsyncClient(base_url="https://cm.example", transport=transport)
    )


async def test_http_client_reads_permissions_key_and_forwards_bearer() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "permissions": [
                    {
                        "module": "ATLAS",
                        "resource": "SCHEMA",
                        "action": "PUBLISH",
                        "scope": "GLOBAL",
                        "anchor_path": None,
                    }
                ]
            },
        )

    grants = await _client_with_transport(handler).get_permissions("tok-123")
    assert grants == [_ATLAS_GRANT]
    assert captured["auth"] == "Bearer tok-123"
    assert captured["path"] == "/api/v1/me/permissions"


async def test_http_client_non_200_raises_client_error() -> None:
    client_obj = _client_with_transport(lambda _req: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(CmPermissionsClientError) as exc:
        await client_obj.get_permissions("tok")
    assert exc.value.status_code == 500


async def test_http_client_timeout_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(CmPermissionsUnavailableError):
        await _client_with_transport(handler).get_permissions("tok")


async def test_http_client_connect_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(CmPermissionsUnavailableError):
        await _client_with_transport(handler).get_permissions("tok")


async def test_http_client_malformed_body_raises_client_error() -> None:
    # 200 but the envelope is not the expected {"permissions": [...]} shape.
    client_obj = _client_with_transport(lambda _req: httpx.Response(200, json={"grants": []}))
    with pytest.raises(CmPermissionsClientError):
        await client_obj.get_permissions("tok")


# -- MAPPING: pure helper --------------------------------------------------------


def test_roles_from_grants_maps_atlas_tuple() -> None:
    assert roles_from_grants([_ATLAS_GRANT]) == frozenset({"atlas:schema:publish"})


def test_roles_from_grants_ignores_unrelated_and_anchor() -> None:
    # Unrelated tuple contributes nothing; anchor_path is not part of the key.
    assert roles_from_grants([_UNRELATED_GRANT]) == frozenset()
    anchored_atlas = PermissionGrant("ATLAS", "SCHEMA", "PUBLISH", "GLOBAL", anchor_path="ignored")
    assert roles_from_grants([anchored_atlas]) == frozenset({"atlas:schema:publish"})


def test_roles_from_grants_empty_is_empty() -> None:
    assert roles_from_grants([]) == frozenset()
