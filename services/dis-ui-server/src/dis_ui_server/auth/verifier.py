"""DEV-STUB token verifier — the single seam the 13b JWKS swap replaces.

Verifies the HMAC HS256 dev token byte-identically to the UI's ``/dev/login``
stub (contract §2.1; ``services/dis-ui/src/auth/dev/devStubSecret.ts`` /
``signStubToken.ts``), so dev tokens round-trip end to end. NOT FOR PRODUCTION:
the secret guards nothing real and is deliberately a constant, not config —
an env override would let the two sides drift. The real Customer Master JWKS
verifier (13b, D25) replaces only :func:`verify_token`; the :class:`Identity`
shape and the ``scope.py`` dependencies are stable.

Claim set (pinned by the UI stub and ``dis-ui-server-contract.md``): ``sub``
(required), ``user_type`` (required, ``TENANT``|``PLATFORM`` — Slice 17b,
reject-on-ambiguous: absent/empty/unknown is a hard 401), ``tenant_id`` /
``store_id`` (optional, string — but a TENANT MUST carry ``tenant_id`` and a
PLATFORM MUST NOT, enforced here), ``roles`` (optional, list of strings; absent
means no roles — deny-by-default, since every gate then fails toward 403). Every
verification failure raises ``AuthTokenError`` with a machine-stable ``reason``;
the token itself and raw claim values are NEVER carried on the error (credential
material).
"""

from __future__ import annotations

from typing import Any

import jwt

from dis_core.errors import AuthTokenError
from dis_ui_server.auth.identity import Identity, UserType

# Contract §2.1 dev-stub parameters — byte-identical to dis-ui's devStubSecret.ts.
DEV_STUB_SECRET = "dis-ui-dev-stub-secret-not-for-production"
DEV_STUB_ISSUER = "https://customer-master.local"
DEV_STUB_AUDIENCE = "dis"
DEV_STUB_ALGORITHM = "HS256"


def _optional_str_claim(claims: dict[str, Any], name: str) -> str | None:
    """A nullable string claim; any other type is a bad-claims failure."""
    value = claims.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AuthTokenError(f"claim {name!r} is not a string", reason="bad_claims")
    return value


def _roles_claim(claims: dict[str, Any]) -> tuple[str, ...]:
    """``roles: string[]`` per the contract; absent means no roles."""
    value = claims.get("roles")
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
        raise AuthTokenError("claim 'roles' is not a list of strings", reason="bad_claims")
    return tuple(value)


def _user_type_claim(claims: dict[str, Any]) -> UserType:
    """The REQUIRED explicit ``user_type`` claim (plain name, Slice 17b).

    Absent, empty, or an unrecognized value is a hard rejection — never defaulted or
    downgraded (reject-on-ambiguous). The claim, not ``tenant_id`` presence, is the
    posture discriminator.
    """
    value = claims.get("user_type")
    if not isinstance(value, str) or not value:
        raise AuthTokenError("claim 'user_type' is missing or empty", reason="bad_claims")
    try:
        return UserType(value)
    except ValueError as exc:
        raise AuthTokenError("claim 'user_type' is not a recognized value", reason="bad_claims") from exc


def verify_token(raw: str) -> Identity:
    """Verify a bearer token and yield the :class:`Identity` it asserts.

    This function is the ONLY place a token is inspected; everything downstream
    consumes the returned ``Identity``. Signature, expiry, issuer, audience,
    and required-claim presence are all enforced; any failure is a 401-mapped
    ``AuthTokenError``.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            raw,
            DEV_STUB_SECRET,
            algorithms=[DEV_STUB_ALGORITHM],
            issuer=DEV_STUB_ISSUER,
            audience=DEV_STUB_AUDIENCE,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthTokenError("token expired", reason="expired") from exc
    except jwt.PyJWTError as exc:
        # Malformed token, bad signature, wrong issuer/audience, missing
        # required claim — all collapse to one 401; the reason stays coarse so
        # the response never aids token forgery.
        raise AuthTokenError("token verification failed", reason="invalid") from exc

    sub = claims["sub"]  # presence enforced by options.require above
    if not isinstance(sub, str) or not sub:
        raise AuthTokenError("claim 'sub' is not a non-empty string", reason="bad_claims")

    # user_type is REQUIRED and EXPLICIT (Slice 17b); the user_type<->tenant_id coherence
    # is enforced HERE, at the single token-inspection seam, so no incoherent scope ever
    # reaches a handler. Reject-on-ambiguous: never defaulted, never downgraded.
    user_type = _user_type_claim(claims)
    tenant_id = _optional_str_claim(claims, "tenant_id")
    if user_type is UserType.TENANT and not tenant_id:
        raise AuthTokenError("TENANT token carries no tenant_id", reason="bad_claims")
    if user_type is UserType.PLATFORM and tenant_id:
        # PLATFORM is see-all; the acted-for tenant is a per-request body field on the
        # write path, never a token claim. A PLATFORM token carrying a real tenant_id is
        # an incoherent scope, rejected (decision 2). null/empty/absent are equivalent.
        raise AuthTokenError("PLATFORM token must not carry a tenant_id claim", reason="bad_claims")

    return Identity(
        user_id=sub,
        tenant_id=tenant_id,
        store_id=_optional_str_claim(claims, "store_id"),
        roles=_roles_claim(claims),
        user_type=user_type,
    )
