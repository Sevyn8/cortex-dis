"""JWKS-based JWT verification helper (the consumer verification path).

This is the standard pyjwt + JWKS recipe a DIS consumer (the receiver auth
middleware, not yet built) will mirror: fetch the JWKS, select the key by ``kid``,
verify signature + ``aud`` + ``iss`` + ``exp``. Slice 2 uses it to satisfy
acceptance criterion 2 ("verifies using the same verification path consuming code
will use").

Lives in dis-testing because no consumer exists yet; when the receiver lands it
should verify the same way (and this helper can move/consolidate into shared code).
"""

from __future__ import annotations

import json
from typing import Any

import jwt


def verify_cm_jwt(
    token: str,
    jwks: dict[str, Any],
    *,
    issuer: str,
    audience: str,
) -> dict[str, Any]:
    """Verify a Customer Master JWT against a JWKS document.

    Returns the decoded claims. Raises ``jwt.PyJWTError`` (or a subclass) on any
    signature, claim, or expiry failure — exactly what a consumer would surface.
    """
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    jwk = _select_key(jwks, kid)
    # from_jwk returns RSAPrivateKey | RSAPublicKey; a JWKS entry is always public.
    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    claims: dict[str, Any] = jwt.decode(
        token,
        key=key,  # type: ignore[arg-type]
        algorithms=[jwk.get("alg", "RS256")],
        audience=audience,
        issuer=issuer,
    )
    return claims


def _select_key(jwks: dict[str, Any], kid: str | None) -> dict[str, Any]:
    keys: list[dict[str, Any]] = jwks.get("keys", [])
    if kid is not None:
        for jwk in keys:
            if jwk.get("kid") == kid:
                return jwk
    if len(keys) == 1:
        return keys[0]
    raise jwt.PyJWTError(f"no JWKS key matched kid={kid!r}")
