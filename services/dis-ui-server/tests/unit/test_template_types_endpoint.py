"""``GET /api/v1/template-types`` (Slice 14d): the vocabulary, tenant-free, from memory.

Over the UNREACHABLE-DB client — serving 200 proves no rls_session / no DB read.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from dis_validation import TEMPLATE_TYPES

TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"


def test_template_types_returns_the_vocabulary(client: TestClient, mint_token: Callable[..., str]) -> None:
    resp = client.get("/api/v1/template-types", headers={"Authorization": f"Bearer {mint_token()}"})
    assert resp.status_code == 200
    body = resp.json()
    assert [t["key"] for t in body] == list(TEMPLATE_TYPES)
    # Each carries key, display_name, description so the UI can offer a pick.
    for obj in body:
        assert set(obj) == {"key", "display_name", "description"}
        assert obj["display_name"] and obj["description"]


def test_template_types_identical_across_callers_no_tenant_context(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    a = client.get("/api/v1/template-types", headers={"Authorization": f"Bearer {mint_token()}"})
    b = client.get(
        "/api/v1/template-types", headers={"Authorization": f"Bearer {mint_token(tenant_id=TENANT_B)}"}
    )
    ops_token = mint_token(tenant_id=None, roles=("dis:ops",), user_type="PLATFORM")
    ops = client.get(
        "/api/v1/template-types",
        headers={"Authorization": f"Bearer {ops_token}"},
    )
    assert a.status_code == b.status_code == ops.status_code == 200
    assert a.content == b.content == ops.content


def test_template_types_requires_authentication(client: TestClient) -> None:
    resp = client.get("/api/v1/template-types")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_token"
