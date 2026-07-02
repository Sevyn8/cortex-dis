"""GET /api/v1/me/roles: resolve the caller's DIS roles from CM, fail-safe.

Through the real app pipeline (the me.router is mounted by api.py, reached via the
``client`` fixture), with a FAKE CM client injected via set_permissions_client (the
2a seam; the conftest autouse fixture resets it). No network.

The endpoint is authenticated-only (get_current_identity) and INFORMATIONAL: a CM
failure degrades to 200 {roles: [], resolved: false}, never 503/500. Only
CmPermissionsClientError is caught, so a genuine bug would still 500.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from fastapi.testclient import TestClient

from dis_core.errors import (
    CmPermissionsClientError,
    CmPermissionsUnavailableError,
)
from dis_ui_server.auth import cm_permissions as cm
from dis_ui_server.auth.cm_permissions import PermissionGrant

_ROLES = "/api/v1/me/roles"

_ATLAS_GRANT = PermissionGrant(
    module="ATLAS", resource="SCHEMA", action="PUBLISH", scope="GLOBAL", anchor_path=None
)
_UNRELATED_GRANT = PermissionGrant(
    module="PRICING_OS", resource="MARKDOWNS", action="APPROVE", scope="STORE", anchor_path="t.r.s"
)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _platform_token(mint_token: Callable[..., str]) -> str:
    # A real CM token carries no roles claim; roles come from the CM resolution.
    return mint_token(sub="u-1", tenant_id=None, roles=None, user_type="PLATFORM")


class _FakeReturning:
    def __init__(self, grants: list[PermissionGrant]) -> None:
        self._grants = grants

    async def get_permissions(self, bearer_token: str) -> list[PermissionGrant]:
        assert bearer_token  # the caller's raw bearer is forwarded
        return self._grants


class _FakeRaising:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def get_permissions(self, bearer_token: str) -> list[PermissionGrant]:
        raise self._exc


def _get(client: TestClient, mint_token: Callable[..., str]) -> Any:
    return client.get(_ROLES, headers=_bearer(_platform_token(mint_token)))


# -- resolved: CM answered -------------------------------------------------------


def test_returns_mapped_role_when_cm_has_atlas_grant(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    cm.set_permissions_client(_FakeReturning([_ATLAS_GRANT]))
    resp = _get(client, mint_token)
    assert resp.status_code == 200
    assert resp.json() == {"roles": ["atlas:schema:publish"], "resolved": True}


def test_returns_empty_when_cm_lacks_atlas_grant(client: TestClient, mint_token: Callable[..., str]) -> None:
    # CM answered (resolved=true) but the caller has no mapped grant.
    cm.set_permissions_client(_FakeReturning([_UNRELATED_GRANT]))
    resp = _get(client, mint_token)
    assert resp.status_code == 200
    assert resp.json() == {"roles": [], "resolved": True}


def test_unions_token_roles_with_cm_roles(client: TestClient, mint_token: Callable[..., str]) -> None:
    # If the token ever carries roles, they union with the CM-resolved set (CM is
    # authoritative; the union mirrors require_super_admin). Empty CM grant here.
    cm.set_permissions_client(_FakeReturning([]))
    token = mint_token(sub="u-1", tenant_id=None, roles=("dis:read",), user_type="PLATFORM")
    resp = client.get(_ROLES, headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json() == {"roles": ["dis:read"], "resolved": True}


# -- fail-safe: CM failed -> 200 resolved=false (never 503/500) -------------------


def test_fail_safe_when_cm_unavailable(client: TestClient, mint_token: Callable[..., str]) -> None:
    cm.set_permissions_client(_FakeRaising(CmPermissionsUnavailableError("CM down")))
    resp = _get(client, mint_token)
    assert resp.status_code == 200
    assert resp.json() == {"roles": [], "resolved": False}


def test_fail_safe_on_non_200(client: TestClient, mint_token: Callable[..., str]) -> None:
    cm.set_permissions_client(_FakeRaising(CmPermissionsClientError("CM 500", status_code=500)))
    resp = _get(client, mint_token)
    assert resp.status_code == 200
    assert resp.json() == {"roles": [], "resolved": False}


def test_fail_safe_when_cm_base_url_unset(client: TestClient, mint_token: Callable[..., str]) -> None:
    # No client injected + CM_BASE_URL unset (unit env sets none): the lazy builder
    # raises CmPermissionsUnavailableError, which the handler degrades to resolved=false.
    cm.set_permissions_client(None)
    resp = _get(client, mint_token)
    assert resp.status_code == 200
    assert resp.json() == {"roles": [], "resolved": False}


def test_real_bug_is_not_masked(lenient_client: TestClient, mint_token: Callable[..., str]) -> None:
    # A non-CmPermissionsClientError (a genuine bug) is NOT caught -> 500, not masked
    # as resolved=false. lenient_client returns the 500 instead of re-raising it.
    cm.set_permissions_client(_FakeRaising(RuntimeError("unexpected bug")))
    resp = lenient_client.get(_ROLES, headers=_bearer(_platform_token(mint_token)))
    assert resp.status_code == 500


# -- auth gate: genuine auth failure still 401 -----------------------------------


def test_missing_token_is_401(client: TestClient) -> None:
    resp = client.get(_ROLES)
    assert resp.status_code == 401


def test_malformed_token_is_401(client: TestClient) -> None:
    resp = client.get(_ROLES, headers=_bearer("not-a-jwt"))
    assert resp.status_code == 401


def test_tenant_caller_can_read_own_roles(client: TestClient, mint_token: Callable[..., str]) -> None:
    # Authenticated-only (get_current_identity), NOT require_tenant: a TENANT caller
    # reads their own roles too. CM returns no elevated grant here.
    cm.set_permissions_client(_FakeReturning([]))
    token = mint_token(sub="t-1", tenant_id="019e5e3c-b5d3-705f-9002-2451c4ca2626", roles=None)
    resp = client.get(_ROLES, headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json() == {"roles": [], "resolved": True}


def test_lenient_client_500_is_envelope(lenient_client: TestClient, mint_token: Callable[..., str]) -> None:
    # Sanity: httpx import kept meaningful — an unexpected error surfaces as a 500
    # (not silently swallowed). Uses lenient_client so the 500 is returned, not raised.
    cm.set_permissions_client(_FakeRaising(httpx.HTTPError("raw httpx error")))
    resp = lenient_client.get(_ROLES, headers=_bearer(_platform_token(mint_token)))
    # httpx.HTTPError is NOT a CmPermissionsClientError -> not caught -> 500.
    assert resp.status_code == 500
