"""Unit tests for the Identity Service client (dis_core.identity).

Transport is stubbed with ``httpx.MockTransport`` so these exercise the client's
request shaping, response parsing, and error mapping without a running server.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest

from dis_core.identity import (
    HttpIdentityClient,
    Identity,
    IdentityClient,
    IdentityClientError,
    IdentityNotFoundError,
    IdentityServiceUnavailableError,
    ValidateResponse,
)

_TENANT_UUID = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees
_STORE_UUID = "019e5e3c-b62e-75e6-ad62-529127ae944a"  # TX-101

IDENTITY_OK = {
    "tenant_id": _TENANT_UUID,
    "store_id": _STORE_UUID,
    "display_code": "buc-ees",
    "store_code": "TX-101",
    "is_active": True,
    "source": "customer_master",
    "metadata": {"pii_policy_version": "v1"},
    "resolved_at": "2026-06-01T12:00:00Z",
}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpIdentityClient:
    transport = httpx.MockTransport(handler)
    inner = httpx.AsyncClient(base_url="http://identity-service-fake", transport=transport)
    return HttpIdentityClient("http://identity-service-fake", client=inner)


async def test_resolve_from_token_returns_identity() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=IDENTITY_OK)

    client = _client(handler)
    identity = await client.resolve_from_token("a.jwt.token")
    await client.aclose()

    assert isinstance(identity, Identity)
    # The load-bearing identity is the internal UUID, typed (D37/D52).
    assert identity.tenant_id == UUID(_TENANT_UUID)
    assert identity.store_id == UUID(_STORE_UUID)
    # The authoritative external codes ride alongside (D55).
    assert identity.display_code == "buc-ees"
    assert identity.store_code == "TX-101"
    assert captured["url"].endswith("/v1/resolve_from_token")
    assert captured["body"] == {"jwt": "a.jwt.token"}


async def test_validate_returns_validate_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/v1/validate")
        # UUID request fields serialise to canonical strings (model_dump mode="json").
        assert json.loads(request.content) == {
            "tenant_id": _TENANT_UUID,
            "store_id": _STORE_UUID,
        }
        return httpx.Response(
            200, json={"exists": True, "is_active": True, "source": "identity_mirror_fallback"}
        )

    client = _client(handler)
    result = await client.validate(UUID(_TENANT_UUID), UUID(_STORE_UUID))
    await client.aclose()

    assert isinstance(result, ValidateResponse)
    assert result.exists is True
    assert result.source == "identity_mirror_fallback"


async def test_404_maps_to_identity_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error_code": "identity_not_found",
                "message": "no such upload session",
                "trace_id": "11111111-1111-4111-8111-111111111111",
            },
        )

    client = _client(handler)
    with pytest.raises(IdentityNotFoundError) as excinfo:
        await client.resolve_from_upload("us_a1b2c3d4e5f6")
    await client.aclose()

    err = excinfo.value
    assert err.status_code == 404
    assert err.error_code == "identity_not_found"
    assert err.trace_id == "11111111-1111-4111-8111-111111111111"


async def test_503_maps_to_unavailable_with_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"Retry-After": "30"},
            json={"error_code": "circuit_open", "message": "customer master down"},
        )

    client = _client(handler)
    with pytest.raises(IdentityServiceUnavailableError) as excinfo:
        await client.resolve_from_endpoint("ec_aabbccddeeff")
    await client.aclose()

    assert excinfo.value.retry_after == 30
    assert excinfo.value.error_code == "circuit_open"


async def test_non_json_error_body_still_raises_base_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream boom")

    client = _client(handler)
    with pytest.raises(IdentityClientError) as excinfo:
        await client.resolve_from_token("x")
    await client.aclose()

    assert excinfo.value.status_code == 500
    assert excinfo.value.error_code is None


def test_http_client_satisfies_protocol() -> None:
    # runtime_checkable Protocol: the concrete client is a structural match.
    client = HttpIdentityClient("http://identity-service-fake")
    assert isinstance(client, IdentityClient)
