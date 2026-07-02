"""Customer Master permissions client + grant->role mapping (Atlas A5, DIS side).

The Atlas console is Super-Admin-only (ADR-ATLAS-001 decision 6). The real
``atlas:schema:publish`` authority is Customer Master issued at global scope; CM
puts NO roles claim on the token, so ``require_super_admin`` cannot read it off
:class:`Identity`. Instead it resolves DB-side: forward the caller's verified
(shared-audience) bearer to CM ``GET /api/v1/me/permissions``, map the returned
permission tuples to DIS role strings (Option A), and require the role.

This module mirrors ``dis_core.identity.client.HttpIdentityClient``: a
:class:`CmPermissionsClient` Protocol every caller programs against, an httpx
``Http`` implementation, and a module-level lazy singleton + setter seam
(:func:`set_permissions_client`) so tests inject a fake with no network (the same
shape as ``auth/verifier.py``'s ``set_verifier``).

FAIL-CLOSED: every CM failure path (timeout, unreachable, non-200, malformed
body, any unexpected error) raises a :class:`CmPermissionsClientError` family
error, which the service maps to HTTP 503. The super-admin gate treats all of
these as a denial; a resolution failure is NEVER a grant. The token is forwarded
but never logged or carried on an error (credential material).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from dis_core.errors import (
    CmPermissionsClientError,
    CmPermissionsUnavailableError,
)
from dis_ui_server.config import API_PREFIX, UiServerConfig

# CM mounts the caller-context endpoints under the same ``/api/v1`` prefix DIS
# uses (confirmed: cortex-cm routers/v1/me.py, prefix="/me"). API_PREFIX is
# reused so the two services cannot drift on the version segment.
_ME_PERMISSIONS_PATH = f"{API_PREFIX}/me/permissions"

# The CM /me/permissions envelope key. CONFIRMED against CM's MePermissionsResponse
# (cortex-cm schemas/me.py): the array lives under "permissions" (NOT "grants").
_ENVELOPE_KEY = "permissions"

_DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True)
class PermissionGrant:
    """One CM permission grant, the wire shape of a ``/me/permissions`` item.

    Deliberately local to dis-ui-server (NOT ``dis_core.identity.models``): the
    auth seam keeps CM wire shapes at arm's length. Enum slots arrive as their
    canonical value strings; ``anchor_path`` is nullable and ignored for GLOBAL
    grants by the mapping.
    """

    module: str
    resource: str
    action: str
    scope: str
    anchor_path: str | None


# -- grant -> DIS role mapping (Option A) ---------------------------------------
# Explicit, extensible table. Keyed on the (module, resource, action, scope) tuple;
# ``anchor_path`` is intentionally not part of the key (the only mapping this PR is
# GLOBAL-scoped and unanchored). dis:ops is DEFERRED: CM has no DIS-ops permission
# yet, so there is nothing to map to it (see auth/scope.py require_ops).
_GRANT_TO_ROLE: dict[tuple[str, str, str, str], str] = {
    ("ATLAS", "SCHEMA", "PUBLISH", "GLOBAL"): "atlas:schema:publish",
}


def roles_from_grants(grants: Iterable[PermissionGrant]) -> frozenset[str]:
    """Map CM grants to the DIS role strings the gates check (pure, no I/O).

    Looks each grant's ``(module, resource, action, scope)`` tuple up in
    :data:`_GRANT_TO_ROLE`; unknown tuples contribute nothing. ``anchor_path`` is
    ignored (the mapped grant is GLOBAL/unanchored). Returns the resolved role set.
    """
    return frozenset(
        role
        for grant in grants
        if (role := _GRANT_TO_ROLE.get((grant.module, grant.resource, grant.action, grant.scope))) is not None
    )


@runtime_checkable
class CmPermissionsClient(Protocol):
    """Resolve the caller's CM permission grants from a forwarded bearer token.

    Async because the gate that calls it is async FastAPI. Implementations forward
    the caller's own verified token (shared Auth0 audience) to CM; they never mint
    or introspect it. Tests inject a fake honoring this Protocol.
    """

    async def get_permissions(self, bearer_token: str) -> list[PermissionGrant]: ...


class HttpCmPermissionsClient:
    """HTTP implementation of :class:`CmPermissionsClient` (talks CM's contract).

    Mirrors ``HttpIdentityClient``. Pass ``base_url`` from ``CM_BASE_URL``; an
    ``httpx.AsyncClient`` may be injected (e.g. a MockTransport double) for tests,
    otherwise one is created and owned here. Forwards the caller's bearer per call
    (not a service token), so the base client carries no static Authorization.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def __aenter__(self) -> HttpCmPermissionsClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_permissions(self, bearer_token: str) -> list[PermissionGrant]:
        """GET the caller's grants from CM, forwarding their bearer. Fail-closed.

        Transport failures (timeout, connection error) raise
        :class:`CmPermissionsUnavailableError`; any non-2xx raises
        :class:`CmPermissionsClientError`; a malformed 200 body raises
        :class:`CmPermissionsClientError`. None of these ever grants.
        """
        try:
            response = await self._client.get(
                _ME_PERMISSIONS_PATH,
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
        except httpx.TimeoutException as exc:
            raise CmPermissionsUnavailableError("Customer Master permissions request timed out") from exc
        except httpx.TransportError as exc:
            raise CmPermissionsUnavailableError(
                "Customer Master permissions request failed to connect"
            ) from exc

        if not response.is_success:
            raise CmPermissionsClientError(
                f"Customer Master returned HTTP {response.status_code} for /me/permissions",
                status_code=response.status_code,
            )
        return _parse_permissions(response)


def _parse_permissions(response: httpx.Response) -> list[PermissionGrant]:
    """Parse the ``{"permissions": [...]}`` body into grants (fail-closed on drift)."""
    try:
        body: Any = response.json()
        items = body[_ENVELOPE_KEY]
        return [
            PermissionGrant(
                module=str(item["module"]),
                resource=str(item["resource"]),
                action=str(item["action"]),
                scope=str(item["scope"]),
                anchor_path=item.get("anchor_path"),
            )
            for item in items
        ]
    except (ValueError, KeyError, TypeError, httpx.DecodingError) as exc:
        raise CmPermissionsClientError(
            "Customer Master /me/permissions body was not the expected shape",
            status_code=response.status_code,
        ) from exc


# -- lazy singleton seam (mirrors auth/verifier.py set_verifier/_get_verifier) --
# Built once from config on first use (the httpx client is itself lazy: no I/O
# until the first request), so import stays I/O-free. Tests preempt the build via
# set_permissions_client(), so they never touch config or the network.
_permissions_client: CmPermissionsClient | None = None


def _get_permissions_client() -> CmPermissionsClient:
    """Return the CM permissions client, building it from config on first use.

    FAIL-CLOSED on missing config: ``CM_BASE_URL`` is optional
    (:class:`~dis_ui_server.config.UiServerConfig`), so when it is unset there is
    no CM to call and this raises :class:`CmPermissionsUnavailableError` (-> 503
    denial) rather than granting or crashing the service.
    """
    global _permissions_client
    if _permissions_client is None:
        base_url = UiServerConfig.from_env().cm_base_url
        if not base_url:
            raise CmPermissionsUnavailableError(
                "CM_BASE_URL is not configured; cannot resolve the Atlas Super Admin "
                "authority from Customer Master"
            )
        _permissions_client = HttpCmPermissionsClient(base_url)
    return _permissions_client


def set_permissions_client(client: CmPermissionsClient | None) -> None:
    """Inject a client (tests) or reset the lazy singleton (pass ``None``).

    The test seam mirroring ``auth/verifier.py``'s ``set_verifier``: a fake
    :class:`CmPermissionsClient` resolves in-process with no network. Passing
    ``None`` clears the cache so the next call rebuilds from config.
    """
    global _permissions_client
    _permissions_client = client
