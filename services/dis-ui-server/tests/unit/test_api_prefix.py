"""The /api/v1 prefix mechanism (slice Task 1, acceptance criterion 7).

Routers passed through the app factory mount under the deployed base; the
probes stay at the root and do NOT also exist under the prefix.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_api_routes_mount_under_the_prefix(client: TestClient) -> None:
    assert client.get("/api/v1/probe/ping").status_code == 200
    assert client.get("/api/v1/probe/ping").json() == {"pong": True}


def test_api_routes_do_not_exist_at_the_root(client: TestClient) -> None:
    assert client.get("/probe/ping").status_code == 404


def test_probes_stay_at_the_root_only(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/healthz").status_code == 404
    assert client.get("/api/v1/readyz").status_code == 404
