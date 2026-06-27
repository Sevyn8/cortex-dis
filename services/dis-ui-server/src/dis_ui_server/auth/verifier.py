"""Customer Master / Auth0 token verifier (13b, D25) — the single auth seam.

Verifies a real Customer-Master-issued RS256 bearer token against the Auth0
JWKS: fetch the JWKS, select the signing key by ``kid``, verify signature +
``iss`` + ``aud`` + ``exp``, then read the claims. This replaces the retired
HS256 dev-stub; per the seam contract, ONLY :func:`verify_token` changes — the
:class:`Identity` shape and the ``scope.py`` dependencies are stable.

Verifier parameters come from CONFIG (``config.py``: ``jwt_issuer``,
``jwt_audience``, ``jwt_jwks_url``), NOT hardcoded constants — the real verifier
must be steerable per environment. Issuer is ``https://sevyn8.us.auth0.com/``
(trailing slash), audience is ``https://api.cortex.sevyn8.com`` (SHARED with
Customer Master), JWKS URL derives from the issuer when unset.

Claim set. Customer Master issues its application claims under the
``https://sevyn8.com/`` namespace (the Auth0 custom-claim convention); ``sub``
stays the standard Auth0 subject (``auth0|…``) and is the signature anchor:

- ``sub`` — standard, required (the require list). NOT the principal id.
- ``https://sevyn8.com/user_id`` — required, non-empty; the principal id
  (Customer Master's internal UUID, the value DIS RLS/scoping needs). It, not
  ``sub``, populates :attr:`Identity.user_id`.
- ``https://sevyn8.com/user_type`` — required (``TENANT``|``PLATFORM``),
  reject-on-ambiguous: absent/empty/unknown is a hard 401.
- ``https://sevyn8.com/tenant_id`` / ``https://sevyn8.com/store_id`` — optional
  string; a TENANT MUST carry ``tenant_id`` and a PLATFORM MUST NOT (enforced
  here, unchanged). ``store_id`` is namespaced when CM sends it, absent otherwise.
- ``https://sevyn8.com/roles`` — optional list of strings. Customer Master issues
  NO roles claim today, so this is read IF PRESENT, else the empty tuple
  (deny-by-default, unchanged shape). CONSEQUENCE (step 1 of conformance): the
  ``dis:ops`` / Atlas super-admin gates (``scope.py`` ``require_ops``,
  ``require_super_admin``, ``require_read_scope`` PLATFORM see-all) will DENY
  real CM tokens (403) until step 2 wires DB-side role resolution. That is
  fail-safe and expected; this PR is the verifier swap only.

FAIL-CLOSED. Every verification failure — JWKS unreachable, ``kid`` not found,
bad signature, wrong ``iss``/``aud``, expired, malformed — raises
``AuthTokenError`` with a coarse machine-stable ``reason``; the token itself and
raw claim values are NEVER carried on the error (credential material). The JWKS
fetch + decode live in one try block; claim-extraction errors are raised AFTER
it, so the broad fail-closed backstop can never mask a precise ``bad_claims``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from dis_core.errors import AuthTokenError
from dis_ui_server.auth.identity import Identity, UserType
from dis_ui_server.config import UiServerConfig

# Customer Master's Auth0 custom-claim namespace (the ``https://`` form is a URI
# identifier, not a fetched URL).
_CLAIMS_NAMESPACE = "https://sevyn8.com/"
_CLAIM_USER_ID = _CLAIMS_NAMESPACE + "user_id"
_CLAIM_USER_TYPE = _CLAIMS_NAMESPACE + "user_type"
_CLAIM_TENANT_ID = _CLAIMS_NAMESPACE + "tenant_id"
_CLAIM_STORE_ID = _CLAIMS_NAMESPACE + "store_id"
_CLAIM_ROLES = _CLAIMS_NAMESPACE + "roles"

# Auth0 signs access tokens with RS256; we accept that algorithm only (never a
# client-asserted ``alg``, never a symmetric algorithm — that is the classic
# JWKS confusion attack).
_ALGORITHMS = ["RS256"]


def _optional_str_claim(claims: dict[str, Any], name: str) -> str | None:
    """A nullable string claim; any other type is a bad-claims failure."""
    value = claims.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AuthTokenError(f"claim {name!r} is not a string", reason="bad_claims")
    return value


def _required_str_claim(claims: dict[str, Any], name: str) -> str:
    """A required, non-empty string claim; absent/empty/non-string is bad-claims."""
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise AuthTokenError(f"claim {name!r} is missing or not a non-empty string", reason="bad_claims")
    return value


def _roles_claim(claims: dict[str, Any], name: str) -> tuple[str, ...]:
    """``roles: string[]`` under ``name``; absent means no roles (deny-by-default)."""
    value = claims.get(name)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
        raise AuthTokenError(f"claim {name!r} is not a list of strings", reason="bad_claims")
    return tuple(value)


def _user_type_claim(claims: dict[str, Any], name: str) -> UserType:
    """The REQUIRED explicit ``user_type`` claim (read from ``name``, Slice 17b).

    Absent, empty, or an unrecognized value is a hard rejection — never defaulted or
    downgraded (reject-on-ambiguous). The claim, not ``tenant_id`` presence, is the
    posture discriminator.
    """
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise AuthTokenError("claim 'user_type' is missing or empty", reason="bad_claims")
    try:
        return UserType(value)
    except ValueError as exc:
        raise AuthTokenError("claim 'user_type' is not a recognized value", reason="bad_claims") from exc


class SigningKeyClient(Protocol):
    """The JWKS signing-key resolver the verifier depends on.

    Production is :class:`jwt.PyJWKClient` (fetch JWKS, cache, select by ``kid``);
    tests inject a no-network double. The returned object exposes ``.key`` (the
    public key), matching ``jwt.PyJWK``.
    """

    def get_signing_key_from_jwt(self, token: str, /) -> Any: ...


@dataclass(frozen=True)
class TokenVerifier:
    """An issuer/audience-pinned RS256/JWKS verifier.

    The verification mechanics live here; :func:`verify_token` is the stable seam
    that ``scope.py`` calls. Construct via :func:`_get_verifier` (config-resolved,
    lazy) in production, or inject in tests via :func:`set_verifier`.
    """

    issuer: str
    audience: str
    jwk_client: SigningKeyClient

    def verify(self, raw: str) -> Identity:
        """Verify ``raw`` and yield the :class:`Identity` it asserts (fail-closed)."""
        try:
            signing_key = self.jwk_client.get_signing_key_from_jwt(raw)
            claims: dict[str, Any] = jwt.decode(
                raw,
                signing_key.key,
                algorithms=_ALGORITHMS,
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthTokenError("token expired", reason="expired") from exc
        except jwt.PyJWTError as exc:
            # Malformed token, bad signature, wrong issuer/audience, missing
            # required claim, kid-not-found (PyJWKClientError) and JWKS
            # unreachable (PyJWKClientConnectionError) are all PyJWTError
            # subclasses — every one collapses to one 401; the reason stays
            # coarse so the response never aids token forgery.
            raise AuthTokenError("token verification failed", reason="invalid") from exc
        except Exception as exc:
            # Fail-closed backstop: ANY unexpected error during JWKS resolution or
            # decode denies coarsely. This is a re-raise (translation), not a
            # swallow — it never reaches a handler with an unverified token.
            raise AuthTokenError("token verification failed", reason="invalid") from exc

        # --- claim extraction (raised AFTER the decode block, so the fail-closed
        # backstop above can never mask a precise bad_claims rejection) ---

        # ``sub`` stays the standard Auth0 subject (the signature anchor); it is
        # required (options.require) and validated, but is NOT the principal id.
        _required_str_claim(claims, "sub")
        # The principal id is the namespaced Customer Master internal UUID.
        user_id = _required_str_claim(claims, _CLAIM_USER_ID)

        # user_type is REQUIRED and EXPLICIT (Slice 17b); the user_type<->tenant_id
        # coherence is enforced HERE, at the single token-inspection seam, so no
        # incoherent scope ever reaches a handler. Reject-on-ambiguous: never
        # defaulted, never downgraded.
        user_type = _user_type_claim(claims, _CLAIM_USER_TYPE)
        tenant_id = _optional_str_claim(claims, _CLAIM_TENANT_ID)
        if user_type is UserType.TENANT and not tenant_id:
            raise AuthTokenError("TENANT token carries no tenant_id", reason="bad_claims")
        if user_type is UserType.PLATFORM and tenant_id:
            # PLATFORM is see-all; the acted-for tenant is a per-request body field on
            # the write path, never a token claim. A PLATFORM token carrying a real
            # tenant_id is an incoherent scope, rejected (decision 2). null/empty/absent
            # are equivalent.
            raise AuthTokenError("PLATFORM token must not carry a tenant_id claim", reason="bad_claims")

        return Identity(
            user_id=user_id,
            tenant_id=tenant_id,
            store_id=_optional_str_claim(claims, _CLAIM_STORE_ID),
            roles=_roles_claim(claims, _CLAIM_ROLES),
            user_type=user_type,
        )


# Module-level lazy singleton. Built once from config on first use (PyJWKClient is
# itself lazy: no JWKS fetch until the first token), so import stays I/O-free and
# the liveness/readiness split is unaffected. Tests preempt the build via
# set_verifier(), so they never touch config or the network.
_verifier: TokenVerifier | None = None


def _get_verifier() -> TokenVerifier:
    global _verifier
    if _verifier is None:
        config = UiServerConfig.from_env()
        _verifier = TokenVerifier(
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
            jwk_client=jwt.PyJWKClient(config.jwt_jwks_url),
        )
    return _verifier


def set_verifier(verifier: TokenVerifier | None) -> None:
    """Inject a verifier (tests) or reset the lazy singleton (pass ``None``).

    The test seam: a :class:`TokenVerifier` built over a no-network signing-key
    client verifies in-process with no JWKS fetch. Passing ``None`` clears the
    cache so the next call rebuilds from config.
    """
    global _verifier
    _verifier = verifier


def verify_token(raw: str) -> Identity:
    """Verify a bearer token and yield the :class:`Identity` it asserts.

    This function is the ONLY place a token is inspected; everything downstream
    consumes the returned ``Identity``. Signature, expiry, issuer, audience,
    and required-claim presence are all enforced; any failure is a 401-mapped
    ``AuthTokenError`` (fail-closed).
    """
    return _get_verifier().verify(raw)
