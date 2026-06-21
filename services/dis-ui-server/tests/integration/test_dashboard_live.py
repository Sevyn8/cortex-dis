"""``GET /api/v1/dashboard/metrics`` against the LIVE stack (read-only Dashboard reads).

Proves the new aggregate SQL is VALID against the real schemas (audit.events,
quarantine.quarantined_rows/_chunks, canonical.*) and runs tenant-scoped through
``rls_session`` for two distinct tenants. Counts depend on seed state, so this
asserts the SHAPE, the types, non-negativity, the canonical table set, and the
quarantine-rate rule (null-or-float) rather than fragile exact numbers; the
exact-count behaviour is pinned by the DB-free unit test (mapping) and is best
spot-checked against the staging tenant that carries real canonical rows.

Loud-error posture (the Slice 4/7/8 lesson): a missing stack env var ERRORS, never skips.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from dis_ui_server.main import create_app

pytestmark = pytest.mark.integration

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees (live seed)
TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"  # zabka-group (live seed)

_CANONICAL_TABLES = {
    "store_sku_current_position",
    "store_sku_sale_events",
    "store_sku_change_events",
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def live_client(stack_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("POSTGRES_URL", stack_env["POSTGRES_URL"])
    with TestClient(create_app()) as client:
        yield client


def _assert_well_shaped(body: dict[str, object]) -> None:
    assert isinstance(body["rows_ingested_24h"], int)
    assert body["rows_ingested_24h"] >= 0

    q = body["quarantine_24h"]
    assert isinstance(q, dict)
    assert isinstance(q["quarantined_rows"], int) and q["quarantined_rows"] >= 0
    assert isinstance(q["received_rows"], int) and q["received_rows"] >= 0
    # rate is null when nothing was received, else a float in [0, ...]; never fabricated.
    if q["received_rows"] == 0:
        assert q["rate"] is None
    else:
        assert isinstance(q["rate"], (int, float))

    rc = body["records_in_canonical"]
    assert isinstance(rc, dict)
    assert isinstance(rc["total"], int) and rc["total"] >= 0
    by_table = {c["table"]: c["count"] for c in rc["by_table"]}
    assert set(by_table) == _CANONICAL_TABLES
    assert all(isinstance(v, int) and v >= 0 for v in by_table.values())
    assert rc["total"] == sum(by_table.values())

    assert isinstance(body["flow"], list)
    for row in body["flow"]:
        assert isinstance(row["rows_24h"], int) and row["rows_24h"] >= 0
        assert row["last_received_at"] is None or isinstance(row["last_received_at"], str)


def test_dashboard_metrics_valid_and_scoped_for_tenant_a(
    live_client: TestClient, mint_token: Callable[..., str]
) -> None:
    resp = live_client.get("/api/v1/dashboard/metrics", headers=_bearer(mint_token(tenant_id=TENANT_A)))
    assert resp.status_code == 200
    _assert_well_shaped(resp.json())


def test_dashboard_metrics_valid_for_tenant_b(
    live_client: TestClient, mint_token: Callable[..., str]
) -> None:
    # A second, distinct tenant: the same reads run under that tenant's RLS scope.
    resp = live_client.get("/api/v1/dashboard/metrics", headers=_bearer(mint_token(tenant_id=TENANT_B)))
    assert resp.status_code == 200
    _assert_well_shaped(resp.json())


def test_dashboard_metrics_requires_a_token(live_client: TestClient) -> None:
    assert live_client.get("/api/v1/dashboard/metrics").status_code == 401
