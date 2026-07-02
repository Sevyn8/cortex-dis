"""``GET /me/roles`` - the caller's resolved DIS role strings (DIS step 2).

Authenticated-only (``get_current_identity``): any signed-in caller reads their OWN
roles - this is NOT a privileged gate (ops/platform callers carry no tenant, so
``require_tenant`` would wrongly exclude them). Resolution reuses step 2a's CM
client + ``roles_from_grants`` mapping (the SAME tuple->role table the
``require_super_admin`` gate uses), forwarding the caller's own verified bearer to
Customer Master; the tuple->role mapping is never duplicated (here or in the SPA).

FAIL-SAFE: unlike the ``require_super_admin`` gate (which 503s on a CM failure to
deny a protected action), this is an informational self-read. A CM error /
unavailable / unset ``CM_BASE_URL`` degrades to HTTP 200 with
``{"roles": [], "resolved": false}`` so the SPA can hide role surfaces while
tenant-default surfaces keep working - never a 503, never a 500. Only
``CmPermissionsClientError`` (the documented CM-failure family, incl. the
unavailable subclass) is caught; a genuine bug still surfaces as 500. A missing /
malformed / expired token still 401s upstream in ``get_current_identity``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from dis_core.errors import CmPermissionsClientError
from dis_ui_server.auth.cm_permissions import (
    _get_permissions_client,
    roles_from_grants,
)
from dis_ui_server.auth.identity import Identity
from dis_ui_server.auth.scope import _extract_bearer, get_current_identity
from dis_ui_server.schemas.me import MeRolesResponse

router = APIRouter()


@router.get("/me/roles", response_model=MeRolesResponse)
async def get_my_roles(
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> MeRolesResponse:
    """Resolve the caller's DIS roles from Customer Master (fail-safe)."""
    token = _extract_bearer(request)
    try:
        grants = await _get_permissions_client().get_permissions(token)
    except CmPermissionsClientError:
        # Informational self-read: a CM failure hides role surfaces (roles=[]) but
        # does not fail the request. resolved=false is the explicit fail-safe signal.
        return MeRolesResponse(roles=[], resolved=False)
    # CM is authoritative; union with any token roles (empty today, harmless) mirrors
    # the require_super_admin resolution. Sorted for a stable wire shape.
    roles = sorted(roles_from_grants(grants) | set(identity.roles))
    return MeRolesResponse(roles=roles, resolved=True)
