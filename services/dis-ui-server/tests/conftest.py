"""Shared fixtures for the Slice 13a tests (one conftest: unit + integration).

UNIT half — no stack, no real database. The app under test is built through
the production factory (``create_app``) with PROBE routers passed through the
test seam, so every assertion runs the real pipeline: the ``/api/v1`` include
mechanism, the auth dependencies, and the registered exception handlers.
``POSTGRES_URL`` points at a parseable but UNREACHABLE address (a
non-listening localhost port): startup must succeed (the engine is lazy),
``/healthz`` must serve, and ``/readyz`` must degrade — the
liveness/readiness split these tests pin. Tokens are minted in-test with the
contract §2.1 dev-stub parameters (byte-identical to dis-ui's ``/dev/login``);
NOT the dis-testing RS256 CM-fake fixtures, which are the 13b JWKS target.

INTEGRATION half — proves ``/readyz`` against the LIVE local stack
(``ithina_dis_db`` on 5433; Customer Master on 5432 is never touched), so —
the Slice 4/7 lesson — those tests must NOT skip silently when the stack is
absent: a missing env var is a loud ERROR (``StackRequiredError``), never a
skip. Read-only by construction: the readiness probe runs one scoped
``SELECT`` under a fresh synthetic tenant; no rows are written.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Annotated, Any, Protocol

import jwt
import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel

from dis_core.errors import (
    AuthTokenError,
    MappingStateConflictError,
    MappingTemplateNameConflictError,
    MirrorSyncError,
    OpsRoleRequiredError,
    ResourceNotFoundError,
    RlsContextError,
    TenantScopeError,
)
from dis_core.trace_id import bind_trace_id, new_trace_id
from dis_ui_server.auth.identity import Identity
from dis_ui_server.auth.scope import require_ops, require_tenant
from dis_ui_server.auth.verifier import (
    DEV_STUB_ALGORITHM,
    DEV_STUB_AUDIENCE,
    DEV_STUB_ISSUER,
    DEV_STUB_SECRET,
)
from dis_ui_server.main import create_app

# -- unit fixtures ----------------------------------------------------------------

# Parseable URL, nothing listens: startup must survive it, /readyz must not.
UNREACHABLE_POSTGRES_URL = "postgresql+psycopg://u:p@127.0.0.1:9/ithina_dis_db"


def set_unit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unit-test environment: required config present, every backend unreachable.

    Slice 8 made GCS_BUCKET_BRONZE + PUBSUB_PROJECT_ID required (crashloop on
    missing, same posture as POSTGRES_URL); the upload dependencies are
    construction-lazy, so unreachable emulator hosts keep startup green while
    any actual I/O would fail loudly — unit tests override ``app.state`` with
    fakes instead of reaching them.
    """
    monkeypatch.setenv("POSTGRES_URL", UNREACHABLE_POSTGRES_URL)
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "ithina-bronze-raw")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:9")  # construction guard only
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:9")


TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees
TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"  # zabka-group


class TokenMinter(Protocol):
    def __call__(
        self,
        *,
        sub: str = ...,
        tenant_id: str | None = ...,
        store_id: str | None = ...,
        roles: tuple[str, ...] | None = ...,
        user_type: str | None = ...,
        expires_in: int = ...,
        issuer: str = ...,
        audience: str = ...,
        secret: str = ...,
        omit: tuple[str, ...] = ...,
    ) -> str: ...


@pytest.fixture
def mint_token() -> TokenMinter:
    """Mint dev-stub-shaped HS256 tokens, with knobs for every failure mode.

    Slice 17b adds the required ``user_type`` claim (default ``"TENANT"`` so existing
    callers stay valid under the now-mandatory claim). The 3 interim token personas
    (token contract; the impersonation TARGET is a request-body field, NOT a claim):

    - TENANT:               mint_token(user_type="TENANT", tenant_id=<uuid>, roles=("dis:read",))
    - PLATFORM see-all:     mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:ops", "dis:read"))
    - PLATFORM impersonate: SAME token as see-all (PLATFORM + dis:ops, no tenant_id); the
                            acted-for tenant rides the POST/PATCH body ``acting_for_tenant_id``.

    Reject-on-ambiguous knobs: ``user_type=None`` (or ``omit=("user_type",)``) -> absent;
    ``user_type=""`` -> empty; ``user_type="BOGUS"`` -> unrecognized.
    """

    def _mint(
        *,
        sub: str = "user-1",
        tenant_id: str | None = TENANT_A,
        store_id: str | None = None,
        roles: tuple[str, ...] | None = ("dis:read",),
        user_type: str | None = "TENANT",
        expires_in: int = 3600,
        issuer: str = DEV_STUB_ISSUER,
        audience: str = DEV_STUB_AUDIENCE,
        secret: str = DEV_STUB_SECRET,
        omit: tuple[str, ...] = (),
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": sub,
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + expires_in,
        }
        if tenant_id is not None:
            payload["tenant_id"] = tenant_id
        if store_id is not None:
            payload["store_id"] = store_id
        if roles is not None:
            payload["roles"] = list(roles)
        if user_type is not None:  # Slice 17b required claim; None/""/"BOGUS" exercise reject-on-ambiguous
            payload["user_type"] = user_type
        for claim in omit:
            payload.pop(claim, None)
        return jwt.encode(payload, secret, algorithm=DEV_STUB_ALGORITHM)

    return _mint


class _ValidatedBody(BaseModel):
    value: int


def _probe_router() -> APIRouter:
    """Test-only routes exercising the real dependencies and handlers."""
    router = APIRouter(prefix="/probe")

    @router.get("/ping")
    async def ping() -> dict[str, bool]:  # unauthenticated: the prefix test
        return {"pong": True}

    @router.get("/tenant")
    async def tenant_probe(
        identity: Annotated[Identity, Depends(require_tenant)],
    ) -> dict[str, str | None]:
        return {"tenant_id": identity.tenant_id, "user_id": identity.user_id}

    @router.post("/tenant-echo")
    async def tenant_echo(
        identity: Annotated[Identity, Depends(require_tenant)],
    ) -> dict[str, str | None]:
        # Deliberately declares NO body/query/header params: the only tenant
        # source in reach is the verified token (the foundation-rule probe).
        return {"tenant_id": identity.tenant_id}

    @router.get("/ops")
    async def ops_probe(
        identity: Annotated[Identity, Depends(require_ops)],
    ) -> dict[str, str | None]:
        return {"user_id": identity.user_id, "tenant_id": identity.tenant_id}

    @router.get("/raise/auth-token")
    async def raise_auth() -> None:
        raise AuthTokenError("probe auth failure", reason="probe")

    @router.get("/raise/tenant-scope")
    async def raise_scope() -> None:
        raise TenantScopeError("probe scope failure", tenant_id=TENANT_A)

    @router.get("/raise/ops-role-required")
    async def raise_ops() -> None:
        raise OpsRoleRequiredError("probe ops failure")

    @router.get("/raise/rls-context")
    async def raise_rls() -> None:
        raise RlsContextError("probe rls failure", database="wrong_db", role="some_role")

    # Slice 14b error-family probes: the envelope mapping for the data endpoints.
    @router.get("/raise/resource-not-found")
    async def raise_not_found() -> None:
        raise ResourceNotFoundError(
            "probe not found", resource="mapping_template", identifier="x", tenant_id=TENANT_A
        )

    @router.get("/raise/template-name-conflict")
    async def raise_name_conflict() -> None:
        raise MappingTemplateNameConflictError(
            "probe name conflict", tenant_id=TENANT_A, source_id="src", template_name="sales"
        )

    @router.get("/raise/mapping-state-conflict")
    async def raise_state_conflict() -> None:
        raise MappingStateConflictError(
            "probe state conflict",
            template_id="x",
            tenant_id=TENANT_A,
            expected="DRAFT, STAGED or ACTIVE",
            actual="DEPRECATED",
        )

    @router.get("/raise/unmapped")
    async def raise_unmapped() -> None:
        # A real DisError leaf with NO handler-map entry: must fall back to 500.
        raise MirrorSyncError("probe unmapped failure", tenant_id=TENANT_A)

    @router.get("/raise/traced")
    async def raise_traced() -> None:
        bind_trace_id(new_trace_id())
        raise AuthTokenError("probe traced failure", reason="probe")

    @router.get("/raise/non-dis")
    async def raise_non_dis() -> None:
        # NOT DisError-rooted: must fall through to Starlette's generic 500
        # (no envelope, no message) — internals never echo into a response.
        raise ValueError("sentinel-internal-context-do-not-leak")

    @router.post("/validated")
    async def validated(body: _ValidatedBody) -> dict[str, int]:
        return {"value": body.value}

    return router


@pytest.fixture
def unit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unit env alone — for tests that build their own app/client."""
    set_unit_env(monkeypatch)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app over an unreachable DB, probe routes mounted, lifespan run."""
    set_unit_env(monkeypatch)
    with TestClient(create_app(extra_api_routers=[_probe_router()])) as test_client:
        yield test_client


@pytest.fixture
def lenient_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Like ``client`` but returns server errors as responses instead of
    re-raising them — needed to assert what an UNHANDLED exception actually
    sends over the wire."""
    set_unit_env(monkeypatch)
    app = create_app(extra_api_routers=[_probe_router()])
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# -- integration fixtures -----------------------------------------------------------


class StackRequiredError(RuntimeError):
    """The local stack is required for these load-bearing tests but is absent."""


_REQUIRED_ENV = (
    "POSTGRES_URL",
    "POSTGRES_ADMIN_URL",
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise StackRequiredError(
            f"{name} is not set — the Slice 13a readiness integration tests refuse to "
            "skip silently. Bring up the stack (make run-local) and load .env."
        )
    return value


@pytest.fixture(scope="session")
def stack_env() -> dict[str, str]:
    return {name: _require_env(name) for name in _REQUIRED_ENV}
