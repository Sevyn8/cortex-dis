"""Customer Master fake (FastAPI) for the local devbox and tests.

Stands in for the real Customer Master so DIS can be tested without it. It:
  * issues signed (RS256) test JWTs,
  * publishes a JWKS endpoint for signature verification,
  * creates/serves upload sessions (``us_*``),
  * emits ``identity.changed`` Pub/Sub events on a "change".

HARD BOUNDARIES (slice scope):
  * **No real authentication / authorization.** It signs whatever token is asked
    for; it validates no credentials and enforces no access. The signature +
    JWKS exist only so the *consumer's* verification path is exercised for real.
  * **No identity resolution** (that's the Identity Service fake / Slice 13).

PROVISIONAL (R2/R3): the Customer Master contract is not yet signed off. The JWT
claim set, JWKS shape, issuer/audience, and the very fact that this fake (rather
than the Identity Service) emits ``identity.changed`` are provisional. The emitted
message still validates against the frozen ``identity.changed`` schema; the
publication *topology* is what is provisional. Revisit on CM contract sign-off.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Literal

import jwt
import uuid_utils
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from dis_testing import fixtures as fx
from dis_testing.fakes.pubsub import EmulatorPublisher, Publisher

IDENTITY_CHANGED_TOPIC = "identity.changed"
IDENTITY_CHANGED_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# JWKS + token issuance
# ---------------------------------------------------------------------------
def build_jwks() -> dict[str, object]:
    """Build the JWKS document from the committed test public key."""
    public_key = load_pem_public_key(fx.TEST_RSA_PUBLIC_KEY_PEM.encode())
    # load_pem_public_key returns a broad key union; our committed PEM is RSA.
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))  # type: ignore[arg-type]
    jwk.update({"kid": fx.TEST_JWT_KID, "use": "sig", "alg": fx.TEST_JWT_ALG})
    return {"keys": [jwk]}


def issue_jwt(
    *,
    tenant: fx.TenantFixture,
    store: fx.StoreFixture | None,
    user_id: str = fx.DEFAULT_USER_ID,
    roles: tuple[str, ...] = fx.DEFAULT_ROLES,
    expires_in: int = 3600,
) -> str:
    """Issue a signed (RS256) test JWT carrying the provisional CM claim set."""
    now = int(time.time())
    claims = fx.build_claims(
        tenant, store, user_id=user_id, roles=roles, issued_at=now, expires_at=now + expires_in
    )
    return jwt.encode(
        claims,
        fx.TEST_RSA_PRIVATE_KEY_PEM,
        algorithm=fx.TEST_JWT_ALG,
        headers={"kid": fx.TEST_JWT_KID},
    )


def build_identity_changed(req: ChangeRequest) -> dict[str, object]:
    """Build an ``identity.changed`` message conforming to the frozen schema.

    Identity fields are the internal UUIDs (D52); the payload carries ``status``
    replicated verbatim (D46 — never an ``is_active`` boolean) plus the
    authoritative external code (``display_code`` for tenants, ``store_code`` for
    stores, omitted when the source carries none — D55). A ``deactivated`` event
    maps to the entity's inactive lifecycle status.
    """
    payload: dict[str, object]
    if req.entity == "tenant":
        tenant = fx.tenant_by_display_code(req.code)
        entity_uuid = tenant.uuid
        owning_tenant_uuid = tenant.uuid
        source_ts = tenant.pc_updated_at
        status = "SUSPENDED" if req.event_type == "deactivated" else tenant.status
        payload = {
            "name": tenant.name,
            "status": status,
            "display_code": tenant.display_code,
            "metadata": dict(tenant.metadata),
        }
    else:
        store = fx.store_by_store_code(req.code)
        entity_uuid = store.uuid
        owning_tenant_uuid = fx.tenant_by_display_code(store.tenant_display_code).uuid
        source_ts = store.pc_updated_at
        status = "CLOSED" if req.event_type == "deactivated" else store.status
        payload = {"name": store.name, "status": status, "metadata": {}}
        if store.store_code is not None:
            payload["store_code"] = store.store_code

    return {
        "schema_version": IDENTITY_CHANGED_SCHEMA_VERSION,
        "event_id": str(uuid_utils.uuid7()),
        "event_type": req.event_type,
        "entity": req.entity,
        "entity_id": str(entity_uuid),
        "tenant_id": str(owning_tenant_uuid),
        "source_ts": source_ts.isoformat(),
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class TokenRequest(BaseModel):
    tenant_display_code: str | None = None
    store_code: str | None = None
    user_id: str | None = None
    roles: list[str] | None = None
    expires_in: int = 3600


class TokenResponse(BaseModel):
    jwt: str
    token_type: str = "Bearer"
    expires_in: int


class UploadSessionRequest(BaseModel):
    tenant_display_code: str | None = None
    store_code: str | None = None


class UploadSessionResponse(BaseModel):
    # tenant_id/store_id carry the authoritative external codes (display_code /
    # store_code) — a CM-facing artifact never exposes internal UUIDs. This is an
    # approximation of the unseen real CM API shape, registered in decisions.md
    # for the CM contract sign-off. store_id is None for a code-less store.
    upload_session_id: str
    tenant_id: str
    store_id: str | None
    expires_at: int


class ChangeRequest(BaseModel):
    entity: Literal["tenant", "store"]
    code: str  # display_code (entity=tenant) or store_code (entity=store)
    event_type: Literal["created", "updated", "deactivated"] = "updated"


class ChangeResponse(BaseModel):
    message_id: str
    message: dict[str, object]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(publisher: Publisher | None = None) -> FastAPI:
    """Build the CM fake app.

    ``publisher`` may be injected (tests pass an ``InMemoryPublisher``). If left
    ``None`` it is created lazily as an :class:`EmulatorPublisher` on first publish,
    so importing this module / building the app for unit tests does not require the
    emulator.
    """
    app = FastAPI(title="DIS Customer Master fake", version="0.0.0")
    app.state.publisher = publisher
    # Upload sessions live on the app instance so each app has its own store.
    sessions: dict[str, dict[str, object]] = {}
    app.state.upload_sessions = sessions

    def _publisher() -> Publisher:
        if app.state.publisher is None:
            app.state.publisher = EmulatorPublisher()
        publisher_instance: Publisher = app.state.publisher  # Starlette state is untyped
        return publisher_instance

    def _resolve_tenant_store(
        tenant_display_code: str | None, store_code: str | None
    ) -> tuple[fx.TenantFixture, fx.StoreFixture | None]:
        tenant = fx.tenant_by_display_code(tenant_display_code) if tenant_display_code else fx.PRIMARY_TENANT
        if store_code:
            return tenant, fx.store_by_store_code(store_code)
        if tenant_display_code is None:
            return tenant, fx.PRIMARY_STORE
        stores = fx.stores_for_tenant(tenant.display_code)
        return tenant, (stores[0] if stores else None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/jwks.json")
    def jwks() -> dict[str, object]:
        return build_jwks()

    @app.post("/v1/tokens", response_model=TokenResponse)
    def issue_token(req: TokenRequest) -> TokenResponse:
        tenant, store = _resolve_tenant_store(req.tenant_display_code, req.store_code)
        token = issue_jwt(
            tenant=tenant,
            store=store,
            user_id=req.user_id or fx.DEFAULT_USER_ID,
            roles=tuple(req.roles) if req.roles is not None else fx.DEFAULT_ROLES,
            expires_in=req.expires_in,
        )
        return TokenResponse(jwt=token, expires_in=req.expires_in)

    @app.post("/v1/upload-sessions", response_model=UploadSessionResponse)
    def create_upload_session(req: UploadSessionRequest) -> UploadSessionResponse:
        tenant, store = _resolve_tenant_store(req.tenant_display_code, req.store_code)
        if store is None:
            store = fx.PRIMARY_STORE
        session_id = "us_" + secrets.token_hex(6)  # 12 hex chars -> matches ^us_[a-z0-9]{12}$
        expires_at = int(time.time()) + 3600
        sessions[session_id] = {
            "upload_session_id": session_id,
            "tenant_id": tenant.display_code,
            "store_id": store.store_code,  # None for a code-less store (D55)
            "expires_at": expires_at,
        }
        return UploadSessionResponse(**sessions[session_id])

    @app.get("/v1/upload-sessions/{session_id}", response_model=UploadSessionResponse)
    def get_upload_session(session_id: str) -> UploadSessionResponse:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="upload session not found")
        return UploadSessionResponse(**session)

    @app.post("/v1/changes", response_model=ChangeResponse)
    def emit_change(req: ChangeRequest) -> ChangeResponse:
        message = build_identity_changed(req)
        data = json.dumps(message).encode()
        message_id = _publisher().publish(IDENTITY_CHANGED_TOPIC, data)
        return ChangeResponse(message_id=message_id, message=message)

    return app


# Module-level app for uvicorn (docker-compose). Publisher is created lazily.
app = create_app()
