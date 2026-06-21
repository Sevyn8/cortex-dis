"""GET /api/v1/dashboard/metrics, the DB-free half: auth gate + handler wire-mapping.

The client's database is UNREACHABLE; the repo (which would open an rls_session) is
monkeypatched to a fixed result, so these tests pin the auth posture and the
handler's mapping of raw metric values to the wire shape (incl. the quarantine-rate
rule) WITHOUT any DB touch. The real SQL is exercised by the integration test.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from dis_ui_server.repos.dashboard import DashboardMetricsData, FlowAggRow

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _data(*, received: int, quarantined: int) -> DashboardMetricsData:
    return DashboardMetricsData(
        rows_ingested_24h=received,
        quarantined_rows_24h=quarantined,
        canonical_by_table=[
            ("store_sku_current_position", 65),
            ("store_sku_sale_events", 0),
            ("store_sku_change_events", 0),
        ],
        flow=[
            FlowAggRow(
                template_id="0190ac10-5a00-7000-8a00-0000000000a1",
                rows_24h=received,
                last_received_at=datetime(2026, 6, 9, 9, 12, tzinfo=UTC),
            )
        ],
    )


def _patch_fetch(monkeypatch: pytest.MonkeyPatch, data: DashboardMetricsData) -> None:
    async def _fake_fetch(engine: object, tenant_id: object) -> DashboardMetricsData:
        return data

    monkeypatch.setattr("dis_ui_server.handlers.dashboard.fetch_dashboard_metrics", _fake_fetch)


def test_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/dashboard/metrics").status_code == 401


def test_maps_repo_result_to_wire(
    client: TestClient, mint_token: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fetch(monkeypatch, _data(received=1247, quarantined=3))
    resp = client.get("/api/v1/dashboard/metrics", headers=_bearer(mint_token(tenant_id=TENANT_A)))
    assert resp.status_code == 200
    body = resp.json()

    assert body["rows_ingested_24h"] == 1247

    q = body["quarantine_24h"]
    assert q["quarantined_rows"] == 3
    assert q["received_rows"] == 1247
    assert q["rate"] == pytest.approx(3 / 1247)

    rc = body["records_in_canonical"]
    assert rc["total"] == 65
    assert {c["table"]: c["count"] for c in rc["by_table"]} == {
        "store_sku_current_position": 65,
        "store_sku_sale_events": 0,
        "store_sku_change_events": 0,
    }

    assert body["flow"][0]["template_id"] == "0190ac10-5a00-7000-8a00-0000000000a1"
    assert body["flow"][0]["rows_24h"] == 1247
    assert body["flow"][0]["last_received_at"] == "2026-06-09T09:12:00+00:00"


def test_rate_is_null_when_no_ingest(
    client: TestClient, mint_token: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # No denominator -> rate null (the UI shows "No ingest (24h)"), never a fake 0.
    _patch_fetch(monkeypatch, _data(received=0, quarantined=0))
    resp = client.get("/api/v1/dashboard/metrics", headers=_bearer(mint_token(tenant_id=TENANT_A)))
    assert resp.status_code == 200
    q = resp.json()["quarantine_24h"]
    assert q["received_rows"] == 0
    assert q["rate"] is None
