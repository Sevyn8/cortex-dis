"""CORS for the browser-served dis-ui SPA (slice 14c).

All over the unreachable-DB client (CORS never touches the database). The
allowed origin is the confirmed dis-ui dev origin; the posture under test:
explicit origins only, no wildcard, no credentials grant, and zero effect on
traffic that carries no Origin header (probes, curl).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from dis_core.errors import DisError
from dis_ui_server.config import cors_allowed_origins_from_env
from dis_ui_server.main import create_app

DEV_ORIGIN = "http://localhost:5173"  # services/dis-ui README: "pnpm dev - dev server on ..."
EVIL_ORIGIN = "http://evil.example"

_PREFLIGHT_HEADERS = {
    "Origin": DEV_ORIGIN,
    "Access-Control-Request-Method": "GET",
    "Access-Control-Request-Headers": "authorization",
}


# -- preflight ----------------------------------------------------------------------


def test_preflight_from_the_allowed_origin_succeeds(client: TestClient) -> None:
    response = client.options("/api/v1/stores-onboarded", headers=_PREFLIGHT_HEADERS)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == DEV_ORIGIN
    allowed_methods = response.headers["access-control-allow-methods"]
    for method in ("GET", "POST", "PATCH", "OPTIONS"):
        assert method in allowed_methods
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


def test_preflight_from_a_disallowed_origin_grants_nothing(client: TestClient) -> None:
    response = client.options(
        "/api/v1/stores-onboarded", headers={**_PREFLIGHT_HEADERS, "Origin": EVIL_ORIGIN}
    )
    assert "access-control-allow-origin" not in response.headers


def test_no_credentials_grant(client: TestClient) -> None:
    # allow_credentials=False (Bearer header, no cookies — contract §2.1): the
    # credentials grant must never appear, on preflight or actual responses.
    preflight = client.options("/api/v1/stores-onboarded", headers=_PREFLIGHT_HEADERS)
    assert "access-control-allow-credentials" not in preflight.headers
    actual = client.get("/healthz", headers={"Origin": DEV_ORIGIN})
    assert "access-control-allow-credentials" not in actual.headers


# -- actual cross-origin responses ---------------------------------------------------


def test_error_envelopes_are_readable_cross_origin(client: TestClient) -> None:
    # The middleware wraps the exception handlers: a browser on the allowed
    # origin can READ a 401 envelope (without ACAO the body would be opaque).
    response = client.get("/api/v1/mapping-templates", headers={"Origin": DEV_ORIGIN})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_token"
    assert response.headers["access-control-allow-origin"] == DEV_ORIGIN


def test_disallowed_origin_gets_no_header_on_actual_requests(client: TestClient) -> None:
    response = client.get("/healthz", headers={"Origin": EVIL_ORIGIN})
    assert response.status_code == 200  # the request itself is served...
    assert "access-control-allow-origin" not in response.headers  # ...but not granted


# -- nothing changes for origin-less traffic ------------------------------------------


def test_probes_and_endpoints_unchanged_without_an_origin_header(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert not any(name.lower().startswith("access-control-") for name in health.headers)

    catalog = client.get(
        "/api/v1/template-mapping-fields?template_type=snapshot",
        headers={"Authorization": f"Bearer {mint_token()}"},
    )
    assert catalog.status_code == 200
    assert len(catalog.json()) == 29  # 28 mapping-produced + __ignore__
    assert not any(name.lower().startswith("access-control-") for name in catalog.headers)


# -- config resolution -----------------------------------------------------------------


def test_default_is_the_dev_origin_and_never_a_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    origins = cors_allowed_origins_from_env()
    assert origins == (DEV_ORIGIN,)
    assert "*" not in origins


def test_env_override_displaces_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://a.example:1, http://b.example:2")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@127.0.0.1:9/ithina_dis_db")
    # Slice 8 required config (lazy construction; nothing is reached in this test).
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "ithina-bronze-raw")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:9")
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:9")
    assert cors_allowed_origins_from_env() == ("http://a.example:1", "http://b.example:2")

    with TestClient(create_app()) as overridden_client:
        granted = overridden_client.options(
            "/api/v1/stores-onboarded",
            headers={**_PREFLIGHT_HEADERS, "Origin": "http://a.example:1"},
        )
        assert granted.headers["access-control-allow-origin"] == "http://a.example:1"
        # The dev default is DISPLACED, not appended (explicit config wins whole).
        displaced = overridden_client.options("/api/v1/stores-onboarded", headers=_PREFLIGHT_HEADERS)
        assert "access-control-allow-origin" not in displaced.headers


def test_set_but_empty_origins_fail_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", " , ")
    with pytest.raises(DisError, match="contains no origins"):
        cors_allowed_origins_from_env()
