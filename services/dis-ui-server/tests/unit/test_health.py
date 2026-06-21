"""The liveness/readiness split (slice Tasks 4/7; operator-pinned test design).

Three behaviors, three tests, deliberately separate:

1. DB UNREACHABLE (parseable URL, nothing listening): startup succeeds, the
   app serves — ``/healthz`` 200. This is the test the healthz-DB-free claim
   rides on; it can NOT pass via a startup crash.
2. Same app: ``/readyz`` 503 while ``/healthz`` stays 200 — the split itself.
3. MISSING ``POSTGRES_URL``: lifespan startup aborts loudly (crashloop is the
   correct misconfiguration signal). Never the vehicle for the healthz claim.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dis_core.errors import DisError
from dis_ui_server.main import create_app


def test_healthz_serves_with_db_unreachable(client: TestClient) -> None:
    # The client fixture's POSTGRES_URL points at a non-listening port; the
    # lifespan ran (TestClient context) and the engine was created lazily.
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_degrades_while_healthz_stays_up(client: TestClient) -> None:
    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json() == {"status": "degraded"}
    # Same running app, same dead DB: liveness is unaffected by readiness.
    assert client.get("/healthz").status_code == 200


def test_missing_postgres_url_aborts_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(DisError, match="POSTGRES_URL"):
        with TestClient(create_app()):
            pass  # pragma: no cover — startup must raise before we get here


def test_healthz_is_unauthenticated(client: TestClient) -> None:
    # No Authorization header anywhere in this module; explicit on purpose:
    # probes carry no auth dependency (contract §2.7, infra convention).
    response = client.get("/healthz", headers={})
    assert response.status_code == 200
