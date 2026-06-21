"""``GET /stores-onboarded`` against the live stack — the in-query-scoping proof.

``identity_mirror.stores`` is RLS-OFF (D41): the database gives NO backstop, so
tenant isolation here rests entirely on the repo's ``WHERE tenant_id`` predicate
— the registered 14b weak link. These tests are that predicate's enforcement:
token A must see exactly A's stores, token B exactly B's, and no request-side
input may widen the scope (tenant from token ONLY).

Read-only by construction (one scoped SELECT; nothing written).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from dis_ui_server.main import create_app

pytestmark = pytest.mark.integration

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees
TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"  # zabka-group


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def live_client(stack_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("POSTGRES_URL", stack_env["POSTGRES_URL"])
    with TestClient(create_app()) as client:
        yield client


def test_real_cm_currency_tax_values_are_pinned(
    live_client: TestClient, mint_token: Callable[..., str]
) -> None:
    """Pin the REAL CM currency/tax as LITERALS, not a data-driven echo.

    ``test_token_tenant_sees_exactly_its_stores`` compares the API to whatever the
    mirror holds (faithful-to-mirror, but tautological if the mirror itself were
    wrong). This asserts the concrete real values — buc-ees = USD/EXCLUSIVE,
    zabka-group = PLN/INCLUSIVE — so a wrong currency/tax in the synced mirror (or a
    mis-edited fixture) FAILS here. The API lowercases ``tax_treatment``; ``currency``
    is served as-is (char(3))."""
    for tenant, currency, tax in ((TENANT_A, "USD", "exclusive"), (TENANT_B, "PLN", "inclusive")):
        body = live_client.get(
            "/api/v1/stores-onboarded", headers=_bearer(mint_token(tenant_id=tenant))
        ).json()
        assert body, f"no stores served for tenant {tenant} — run make run-local + sync"
        assert all(s["currency"] == currency for s in body), f"{tenant} currency != {currency}: {body}"
        assert all(s["tax_treatment"] == tax for s in body), f"{tenant} tax_treatment != {tax}: {body}"


def _mirror_truth(stack_env: dict[str, str], tenant_id: str) -> list[dict[str, Any]]:
    """The expected rows straight off the mirror (admin read; RLS-OFF table)."""
    engine = create_engine(stack_env["POSTGRES_ADMIN_URL"])
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT store_id, name, store_code, status, country, timezone, "
                    "currency, tax_treatment FROM identity_mirror.stores "
                    "WHERE tenant_id = :tid ORDER BY name, store_id"
                ),
                {"tid": tenant_id},
            ).mappings()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


def test_token_tenant_sees_exactly_its_stores(
    live_client: TestClient, mint_token: Callable[..., str], stack_env: dict[str, str]
) -> None:
    for tenant in (TENANT_A, TENANT_B):
        expected = _mirror_truth(stack_env, tenant)
        assert expected, f"seed data missing for {tenant} — run make run-local"
        response = live_client.get("/api/v1/stores-onboarded", headers=_bearer(mint_token(tenant_id=tenant)))
        assert response.status_code == 200
        body = response.json()
        # Exactly the tenant's stores, stable (name, store_id) order, all 8 fields.
        assert [UUID(s["store_id"]) for s in body] == [row["store_id"] for row in expected]
        assert [s["name"] for s in body] == [row["name"] for row in expected]
        for served, truth in zip(body, expected, strict=True):
            assert served["store_code"] == truth["store_code"]  # nullable, served as-is
            assert served["status"] == truth["status"].lower()
            assert served["country"] == truth["country"]
            assert served["timezone"] == truth["timezone"]
            assert served["currency"] == truth["currency"]
            assert served["tax_treatment"] == truth["tax_treatment"].lower()


def test_tenant_a_cannot_see_tenant_b_stores(
    live_client: TestClient, mint_token: Callable[..., str], stack_env: dict[str, str]
) -> None:
    b_store_ids = {str(row["store_id"]) for row in _mirror_truth(stack_env, TENANT_B)}
    assert b_store_ids
    response = live_client.get("/api/v1/stores-onboarded", headers=_bearer(mint_token(tenant_id=TENANT_A)))
    assert response.status_code == 200
    served_ids = {s["store_id"] for s in response.json()}
    assert served_ids.isdisjoint(b_store_ids)


def test_well_formed_unknown_tenant_gets_an_empty_list(
    live_client: TestClient, mint_token: Callable[..., str]
) -> None:
    # A verified token for a tenant DIS never mirrored: 200 with [], exactly what
    # a provisioned tenant with no stores would see — no error distinguishes
    # "no such tenant" from "tenant with no data" (no existence oracle).
    from dis_core.ids import new_uuid7

    response = live_client.get(
        "/api/v1/stores-onboarded", headers=_bearer(mint_token(tenant_id=str(new_uuid7())))
    )
    assert response.status_code == 200
    assert response.json() == []


def test_tenant_id_comes_from_the_token_only(
    live_client: TestClient, mint_token: Callable[..., str], stack_env: dict[str, str]
) -> None:
    # A token-A request carrying every plausible smuggling vector for tenant B
    # must still serve tenant A's stores: the handler declares no such inputs,
    # so none can be honoured (the foundation rule, proven on real data).
    b_store_ids = {str(row["store_id"]) for row in _mirror_truth(stack_env, TENANT_B)}
    response = live_client.get(
        f"/api/v1/stores-onboarded?tenant_id={TENANT_B}",
        headers={**_bearer(mint_token(tenant_id=TENANT_A)), "X-Tenant-Id": TENANT_B},
    )
    assert response.status_code == 200
    served_ids = {s["store_id"] for s in response.json()}
    assert served_ids
    assert served_ids.isdisjoint(b_store_ids)
