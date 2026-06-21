"""Unit tests for the Identity Service fake (identity-corrected, Slice 9a).

Every response is validated against the authoritative OpenAPI component schema.
The fake is also driven through ``HttpIdentityClient`` to confirm the drop-in
client interface works against it. Identity model: ``tenant_id``/``store_id``
are the internal UUIDs (the field a caller writes identity from, D37);
``display_code``/``store_code`` ride alongside (D55).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from dis_core.identity import HttpIdentityClient, Identity, IdentityNotFoundError
from dis_testing import fixtures as fx
from dis_testing.fakes.customer_master import issue_jwt
from dis_testing.fakes.identity_service import _identity_for, create_app


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "contracts" / "identity-service").is_dir():
            return parent
    raise RuntimeError("could not locate repo root")


_OPENAPI = yaml.safe_load(
    (_repo_root() / "contracts" / "identity-service" / "identity_service.openapi.yaml").read_text()
)
_REGISTRY = Registry().with_resource(
    "urn:oas", Resource.from_contents(_OPENAPI, default_specification=DRAFT202012)
)


def _validator(component: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"urn:oas#/components/schemas/{component}"},
        registry=_REGISTRY,
        format_checker=FormatChecker(),
    )


def _assert_valid(component: str, instance: Any) -> None:
    _validator(component).validate(instance)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_healthz_and_readyz(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_resolve_from_token_returns_uuid_identity_plus_codes(client: TestClient) -> None:
    token = issue_jwt(tenant=fx.PRIMARY_TENANT, store=fx.PRIMARY_STORE)
    resp = client.post("/v1/resolve_from_token", json={"jwt": token})
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid("Identity", body)
    # The UUIDs are the load-bearing identity — the fields a caller writes from.
    assert body["tenant_id"] == str(fx.PRIMARY_TENANT.uuid)
    assert body["store_id"] == str(fx.PRIMARY_STORE.uuid)
    # The authoritative external codes ride alongside (readability only).
    assert body["display_code"] == fx.PRIMARY_TENANT.display_code
    assert body["store_code"] == fx.PRIMARY_STORE.store_code


def test_resolve_from_upload_conforms_to_identity(client: TestClient) -> None:
    resp = client.post("/v1/resolve_from_upload", json={"upload_session_id": "us_a1b2c3d4e5f6"})
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid("Identity", body)
    assert body["tenant_id"] == str(fx.PRIMARY_TENANT.uuid)
    assert body["display_code"] == fx.PRIMARY_TENANT.display_code


def test_resolve_from_endpoint_conforms_to_identity(client: TestClient) -> None:
    resp = client.post("/v1/resolve_from_endpoint", json={"endpoint_config_id": "ec_aabbccddeeff"})
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid("Identity", body)
    assert body["tenant_id"] == str(fx.PRIMARY_TENANT.uuid)


def test_validate_conforms_to_validate_response(client: TestClient) -> None:
    resp = client.post(
        "/v1/validate",
        json={"tenant_id": str(fx.PRIMARY_TENANT.uuid), "store_id": str(fx.PRIMARY_STORE.uuid)},
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid("ValidateResponse", body)
    assert body["exists"] is True
    assert body["is_active"] is True


def test_validate_unknown_pair_returns_exists_false(client: TestClient) -> None:
    resp = client.post(
        "/v1/validate",
        json={
            "tenant_id": "00000000-0000-7000-8000-000000000001",
            "store_id": "00000000-0000-7000-8000-000000000002",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid("ValidateResponse", body)
    assert body["exists"] is False
    assert body["is_active"] is False


def test_validate_rejects_mismatched_tenant_store_pair(client: TestClient) -> None:
    # A known store under the WRONG tenant does not validate.
    resp = client.post(
        "/v1/validate",
        json={"tenant_id": str(fx.TENANTS[1].uuid), "store_id": str(fx.PRIMARY_STORE.uuid)},
    )
    body = resp.json()
    _assert_valid("ValidateResponse", body)
    assert body["exists"] is False


def _edge_store(monkeypatch: pytest.MonkeyPatch, *, store_code: str | None, status: str) -> fx.StoreFixture:
    """Build a one-off edge store under the primary tenant and inject it into the
    fake's view (fx.STORES + the by-code index). The baseline set is all-coded /
    all-ACTIVE, so code-less and inactive paths are exercised via scoped fixtures
    constructed here, not via a baseline row.
    """
    tenant = fx.PRIMARY_TENANT
    edge = fx.StoreFixture(
        store_code=store_code,
        uuid=UUID("019e5e3c-b6ff-7000-8000-0000000000ed"),
        tenant_display_code=tenant.display_code,
        name="Edge store (test-scoped)",
        status=status,
        country="USA",
        timezone="America/Chicago",
        currency="USD",
        tax_treatment="EXCLUSIVE",
        pc_created_at=fx.PRIMARY_STORE.pc_created_at,
        pc_updated_at=fx.PRIMARY_STORE.pc_updated_at,
    )
    monkeypatch.setattr(fx, "STORES", fx.STORES + (edge,))
    if store_code is not None:
        monkeypatch.setattr(fx, "_STORES_BY_CODE", {**fx._STORES_BY_CODE, store_code: edge})
    return edge


def test_none_coded_store_reachable_by_uuid_validate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Named check (Slice 9a): a store with store_code=None cannot be named by
    # code (faithful to the source), but it is never silently unreachable — the
    # UUID-keyed validate path reaches it. The baseline is all-coded, so the
    # code-less store is a test-scoped edge fixture.
    uncoded = _edge_store(monkeypatch, store_code=None, status="INACTIVE")
    tenant = fx.tenant_by_display_code(uncoded.tenant_display_code)
    resp = client.post(
        "/v1/validate",
        json={"tenant_id": str(tenant.uuid), "store_id": str(uncoded.uuid)},
    )
    body = resp.json()
    _assert_valid("ValidateResponse", body)
    assert body["exists"] is True
    assert body["is_active"] is False  # the None-coded edge store is INACTIVE


def test_identity_omits_store_code_when_fixture_code_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Named check (Slice 9a): the fake's identity builder, given the None-coded
    # fixture, carries store_code=None — the envelope's "populate when present"
    # contract (D55) starts here. Code-less store is a test-scoped edge fixture.
    uncoded = _edge_store(monkeypatch, store_code=None, status="INACTIVE")
    identity = _identity_for(uncoded.tenant_display_code, None)
    # Default store resolution picks the tenant's first store; build the identity
    # for the None-coded store explicitly to pin the omission behaviour.
    explicit = Identity(
        tenant_id=fx.tenant_by_display_code(uncoded.tenant_display_code).uuid,
        store_id=uncoded.uuid,
        display_code=uncoded.tenant_display_code,
        store_code=uncoded.store_code,
        is_active=False,
        source="customer_master",
    )
    assert explicit.store_code is None
    # On the wire the absent code is OMITTED, not null ("populate when present", D55).
    dumped = explicit.model_dump(mode="json", exclude_none=True)
    assert "store_code" not in dumped
    _assert_valid("Identity", dumped)
    # And the default-resolution identity for the same tenant is a coded store.
    assert identity.store_code is not None


def test_inactive_store_resolves_with_is_active_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Baseline stores are all ACTIVE; the inactive path is covered by a test-scoped
    # edge store injected into the fake's view.
    inactive = _edge_store(monkeypatch, store_code="TX-199", status="INACTIVE")
    token = issue_jwt(tenant=fx.PRIMARY_TENANT, store=inactive)
    body = client.post("/v1/resolve_from_token", json={"jwt": token}).json()
    _assert_valid("Identity", body)
    assert body["is_active"] is False


def test_unknown_tenant_token_returns_error_envelope(client: TestClient) -> None:
    # A token whose tenant claim is unknown -> 404 + Error schema.
    unsigned = _unsigned_jwt({"tenant_id": "no-such-tenant", "store_id": "XX-999"})
    resp = client.post("/v1/resolve_from_token", json={"jwt": unsigned})
    assert resp.status_code == 404
    _assert_valid("Error", resp.json())
    assert resp.json()["error_code"] == "identity_not_found"


async def test_drop_in_client_against_fake() -> None:
    # The same HttpIdentityClient consumers will use, pointed at the in-process fake.
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    inner = httpx.AsyncClient(base_url="http://identity-service-fake", transport=transport)
    client = HttpIdentityClient("http://identity-service-fake", client=inner)

    token = issue_jwt(tenant=fx.PRIMARY_TENANT, store=fx.PRIMARY_STORE)
    identity = await client.resolve_from_token(token)
    assert isinstance(identity, Identity)
    assert isinstance(identity.tenant_id, UUID)
    assert identity.tenant_id == fx.PRIMARY_TENANT.uuid
    assert identity.display_code == fx.PRIMARY_TENANT.display_code

    # validate through the client (UUID request fields serialise via mode="json").
    answer = await client.validate(fx.PRIMARY_TENANT.uuid, fx.PRIMARY_STORE.uuid)
    assert answer.exists is True

    with pytest.raises(IdentityNotFoundError):
        await client.resolve_from_token(_unsigned_jwt({"tenant_id": "no-such-tenant"}))

    await client.aclose()


def _unsigned_jwt(claims: dict[str, Any]) -> str:
    # alg=none token: header.payload. (The fake decodes claims without verifying.)
    import base64

    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."
