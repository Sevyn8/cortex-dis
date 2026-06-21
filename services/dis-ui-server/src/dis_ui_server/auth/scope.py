"""Request-scoping dependencies — the SOLE source of ``tenant_id`` (contract §2.2).

``get_current_identity`` is the one dependency every protected handler hangs
off; ``require_tenant`` / ``require_ops`` are the §2.1 variants built on it.
The foundation rule, made structural: ``tenant_id`` (and the derived TENANT /
PLATFORM posture) comes from the verified token ONLY — these dependencies never
read a request body, query parameter, or unverified header, so no handler can
be handed a tenant scope that did not survive verification. A test proves the
token is the only path (slice Task 7).

Errors raise the dis-core auth-seam classes; the service's exception handlers
map them to 401/403 + the §2.3 envelope (never ``HTTPException`` here —
code-quality convention).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from dis_core.errors import AuthTokenError, OpsRoleRequiredError, TenantScopeError
from dis_ui_server.auth.identity import Identity, UserType
from dis_ui_server.auth.verifier import verify_token

OPS_ROLE = "dis:ops"

_BEARER_PREFIX = "Bearer "


async def get_current_identity(request: Request) -> Identity:
    """Resolve the caller's :class:`Identity` from the Authorization header.

    The UI sends ``Authorization: Bearer <token>`` on every call (no cookies,
    no CSRF surface). Anything else is a 401-mapped ``AuthTokenError``.
    """
    header = request.headers.get("Authorization")
    if header is None:
        raise AuthTokenError("Authorization header missing", reason="missing_bearer")
    if not header.startswith(_BEARER_PREFIX):
        raise AuthTokenError("Authorization header is not a Bearer token", reason="missing_bearer")
    return verify_token(header[len(_BEARER_PREFIX) :])


async def require_tenant(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> Identity:
    """Guarantee a TENANT-scoped identity (``tenant_id`` set), else 403."""
    if identity.tenant_id is None:
        raise TenantScopeError(
            "token carries no tenant_id for a tenant-scoped endpoint",
            tenant_id=None,
        )
    return identity


async def require_ops(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> Identity:
    """Guarantee the ``dis:ops`` role (PLATFORM user), else 403."""
    if OPS_ROLE not in identity.roles:
        raise OpsRoleRequiredError(f"{OPS_ROLE} role required for an ops endpoint")
    return identity


def tenant_uuid_of(identity: Identity) -> UUID:
    """The verified tenant claim as a UUID — the form every DB predicate needs.

    Real tenant ids are internal UUIDs (D37/D52); a claim that does not parse is
    a malformed token scope, refused as 403 (never a 500 from a cast deep in a
    query, and never a predicate built from an unparsed string).
    """
    if identity.tenant_id is None:
        raise TenantScopeError(
            "token carries no tenant_id for a tenant-scoped endpoint",
            tenant_id=None,
        )
    try:
        return UUID(identity.tenant_id)
    except ValueError as exc:
        raise TenantScopeError(
            "token tenant_id is not a valid tenant identifier",
            tenant_id=None,  # never echo the malformed claim value
        ) from exc


# ---- Slice 17b: two-GUC read/write scope resolution ----


@dataclass(frozen=True)
class ReadScope:
    """Resolved read visibility for a request (Slice 17b).

    ``is_platform`` True is PLATFORM see-all: the repo opens ``rls_platform_session(None)``
    and reads every tenant. Otherwise the read is pinned to ``tenant_id`` and the repo
    opens ``rls_session(tenant_id)`` — the unchanged TENANT path.
    """

    is_platform: bool
    tenant_id: UUID | None


@dataclass(frozen=True)
class WriteScope:
    """Resolved write posture for a request (Slice 17b). Carries only what the VERIFIED
    token asserts — so the impersonation discriminator is ``user_type``, never a
    client-chosen field. The acted-for tenant is applied by :func:`resolve_acted_for`
    together with the request body.
    """

    user_type: UserType
    token_tenant: UUID | None
    has_ops: bool


async def require_read_scope(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> ReadScope:
    """Resolve read visibility from the verified token (Slice 17b, decision 3).

    PLATFORM see-all requires BOTH ``user_type=PLATFORM`` AND the ``dis:ops`` role
    (defense in depth: ``user_type`` is the discriminator, ``dis:ops`` the second
    factor). A PLATFORM token WITHOUT ``dis:ops`` is denied see-all (403). A TENANT
    identity is pinned to its own tenant regardless of any ``dis:ops`` it also carries.
    """
    if identity.user_type is UserType.PLATFORM:
        if OPS_ROLE not in identity.roles:
            raise OpsRoleRequiredError(f"{OPS_ROLE} role required for PLATFORM cross-tenant read")
        return ReadScope(is_platform=True, tenant_id=None)
    return ReadScope(is_platform=False, tenant_id=tenant_uuid_of(identity))


async def require_write_scope(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> WriteScope:
    """Resolve the write posture from the verified token (Slice 17b).

    A bad/absent token raises here (via ``get_current_identity``) BEFORE the handler
    runs. The acted-for tenant is applied by :func:`resolve_acted_for` together with the
    request body, so the discriminator stays the verified ``user_type``.
    """
    token_tenant = tenant_uuid_of(identity) if identity.user_type is UserType.TENANT else None
    return WriteScope(
        user_type=identity.user_type,
        token_tenant=token_tenant,
        has_ops=OPS_ROLE in identity.roles,
    )


def resolve_acted_for(scope: WriteScope, body_tenant_id: UUID | None) -> UUID:
    """The tenant a write acts on, discriminated by the VERIFIED ``user_type`` — never by
    a client-chosen field (Slice 17b, register decision 2 / revised 2e).

    - TENANT + body names a tenant -> reject (a tenant request may not name an acted-for
      tenant; rejected as 403, never silently ignored).
    - TENANT + absent -> the token tenant (pinned).
    - PLATFORM without ``dis:ops`` -> 403.
    - PLATFORM + ``dis:ops`` + acted-for present -> that tenant (impersonation).
    - PLATFORM + ``dis:ops`` + acted-for absent -> 403 (no see-all writes).

    The policy's tenant-pinned WITH CHECK is the structural backstop behind this resolver.
    """
    if scope.user_type is UserType.TENANT:
        if body_tenant_id is not None:
            raise TenantScopeError(
                "a tenant request must not name an acted-for tenant",
                tenant_id=None,
            )
        if scope.token_tenant is None:  # unreachable: a TENANT token always carries a tenant
            raise TenantScopeError("tenant token carries no tenant scope", tenant_id=None)
        return scope.token_tenant
    # PLATFORM
    if not scope.has_ops:
        raise OpsRoleRequiredError(f"{OPS_ROLE} role required for a PLATFORM impersonation write")
    if body_tenant_id is None:
        raise TenantScopeError(
            "a PLATFORM write must name the acted-for tenant in the request body",
            tenant_id=None,
        )
    return body_tenant_id
