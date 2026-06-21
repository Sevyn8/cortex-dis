"""Unit tests for the mapping-suggestions handler + the GeminiSuggester.

The handler is exercised through the production ``create_app`` factory with the
new router mounted via the ``extra_api_routers`` test seam (so api.py is NOT
edited this phase), a fake suggester injected on ``app.state.gemini``, and
dev-stub Bearer tokens from the shared ``mint_token`` fixture. The GeminiSuggester
is tested directly: key-unset and model-error degrade to the mechanical fallback,
a clean response parses to ``source="llm"``, and a non-catalog target is nulled.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import cast

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dis_ui_server.catalog import build_field_catalog
from dis_ui_server.handlers import mapping_suggestions
from dis_ui_server.main import create_app
from dis_ui_server.schemas.mapping_suggestions import ColumnProfile, Suggestion
from dis_ui_server.suggest.gemini_client import GeminiSuggester

CATALOG = build_field_catalog()
CATALOG_KEYS = {field.key for field in CATALOG}

_BODY = {
    "columns": [{"name": "qty", "inferred_datatype": "integer", "null_pct": 0.0, "sample_values": ["1", "2"]}]
}


class _FakeSuggester:
    """Stands in for app.state.gemini; returns a canned (source, model, suggestions)."""

    def __init__(self, result: tuple[str, str | None, list[Suggestion]]) -> None:
        self._result = result

    async def suggest(self, columns, catalog):  # type: ignore[no-untyped-def]
        return self._result


@pytest.fixture
def suggest_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app with the mapping-suggestions router mounted via the test seam.

    Same unreachable-backend env as the shared ``client`` fixture; the lifespan
    builds ``app.state.field_catalog``. ``app.state.gemini`` is wired by a later
    phase, so each test sets it to a fake after startup.
    """
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@127.0.0.1:9/ithina_dis_db")
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "ithina-bronze-raw")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:9")
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:9")
    app = create_app(extra_api_routers=[mapping_suggestions.router])
    with TestClient(app) as client:
        yield client


# -- handler tests ------------------------------------------------------------------


def _set_gemini(client: TestClient, fake: _FakeSuggester) -> None:
    """Set the fake on the app the fixture built; ``TestClient.app`` is typed as the
    bare ASGI callable (no ``.state``), so narrow it to the concrete FastAPI app."""
    cast(FastAPI, client.app).state.gemini = fake


def test_handler_returns_llm_suggestions(suggest_client: TestClient, mint_token) -> None:  # type: ignore[no-untyped-def]
    _set_gemini(
        suggest_client,
        _FakeSuggester(
            (
                "llm",
                "gemini-2.5-flash",
                [
                    Suggestion(
                        source_column="qty",
                        suggested_target="quantity",
                        confidence=0.9,
                        reasoning="numeric",
                    )
                ],
            )
        ),
    )
    resp = suggest_client.post(
        "/api/v1/mapping-suggestions",
        json=_BODY,
        headers={"Authorization": f"Bearer {mint_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "llm"
    assert body["model"] == "gemini-2.5-flash"
    assert body["suggestions"][0]["suggested_target"] == "quantity"
    assert body["suggestions"][0]["reasoning"] == "numeric"


def test_handler_requires_a_token(suggest_client: TestClient) -> None:
    _set_gemini(suggest_client, _FakeSuggester(("fallback", None, [])))
    resp = suggest_client.post("/api/v1/mapping-suggestions", json=_BODY)
    assert resp.status_code == 401


def test_handler_rejects_a_non_tenant_token(suggest_client: TestClient, mint_token) -> None:  # type: ignore[no-untyped-def]
    _set_gemini(suggest_client, _FakeSuggester(("fallback", None, [])))
    resp = suggest_client.post(
        "/api/v1/mapping-suggestions",
        json=_BODY,
        headers={"Authorization": f"Bearer {mint_token(user_type='PLATFORM', tenant_id=None)}"},
    )
    assert resp.status_code == 403


def test_handler_rejects_a_malformed_profile(suggest_client: TestClient, mint_token) -> None:  # type: ignore[no-untyped-def]
    _set_gemini(suggest_client, _FakeSuggester(("fallback", None, [])))
    resp = suggest_client.post(
        "/api/v1/mapping-suggestions",
        json={"columns": []},  # min_length=1 -> 422
        headers={"Authorization": f"Bearer {mint_token()}"},
    )
    assert resp.status_code == 422


# -- type-aware catalog selection (D90) ---------------------------------------------


class _RecordingSuggester:
    """Records the catalog the handler hands it, so the catalog SELECTION can be asserted."""

    def __init__(self) -> None:
        self.catalog = None

    async def suggest(self, columns, catalog):  # type: ignore[no-untyped-def]
        self.catalog = catalog
        return ("fallback", None, [])


def test_handler_scopes_to_the_per_type_catalog_when_template_type_is_present(
    suggest_client: TestClient,
    mint_token: Callable[..., str],
) -> None:
    # A valid template_type selects THAT type's per-type catalog (snapshot included).
    fake = _RecordingSuggester()
    cast(FastAPI, suggest_client.app).state.gemini = fake
    resp = suggest_client.post(
        "/api/v1/mapping-suggestions",
        json={**_BODY, "template_type": "snapshot"},
        headers={"Authorization": f"Bearer {mint_token()}"},
    )
    assert resp.status_code == 200
    # the exact per-type catalog object the lifespan built for snapshot was passed through
    assert fake.catalog is cast(FastAPI, suggest_client.app).state.field_catalogs["snapshot"]


def test_handler_falls_back_to_the_union_catalog_when_template_type_is_absent(
    suggest_client: TestClient,
    mint_token: Callable[..., str],
) -> None:
    # No template_type -> today's sales+inventory_change union (the /upload flow is unchanged).
    fake = _RecordingSuggester()
    cast(FastAPI, suggest_client.app).state.gemini = fake
    resp = suggest_client.post(
        "/api/v1/mapping-suggestions",
        json=_BODY,
        headers={"Authorization": f"Bearer {mint_token()}"},
    )
    assert resp.status_code == 200
    assert fake.catalog is cast(FastAPI, suggest_client.app).state.field_catalog


def test_handler_400s_on_an_invalid_template_type(suggest_client: TestClient, mint_token) -> None:  # type: ignore[no-untyped-def]
    _set_gemini(suggest_client, _FakeSuggester(("fallback", None, [])))
    resp = suggest_client.post(
        "/api/v1/mapping-suggestions",
        json={**_BODY, "template_type": "not_a_type"},
        headers={"Authorization": f"Bearer {mint_token()}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_template_type"


# -- GeminiSuggester tests ----------------------------------------------------------

_COLUMNS = [ColumnProfile(name="qty", inferred_datatype="integer", null_pct=0.0, sample_values=["1"])]


def test_suggester_falls_back_when_no_key() -> None:
    source, model, suggestions = anyio.run(GeminiSuggester(None, None).suggest, _COLUMNS, CATALOG)
    assert source == "fallback"
    assert model is None
    assert len(suggestions) == 1
    assert suggestions[0].suggested_target in CATALOG_KEYS


def test_suggester_parses_a_clean_llm_response() -> None:
    suggester = GeminiSuggester("a-project", "a-location")
    suggester._call_model = lambda prompt: json.dumps(  # type: ignore[method-assign]
        {
            "suggestions": [
                {
                    "source_column": "qty",
                    "suggested_target": "quantity",
                    "confidence": 0.82,
                    "reasoning": "integer counts",
                    "alternatives": ["unit_sale_price"],
                }
            ]
        }
    )
    source, model, suggestions = anyio.run(suggester.suggest, _COLUMNS, CATALOG)
    assert source == "llm"
    assert model == "gemini-2.5-flash"
    assert suggestions[0].suggested_target == "quantity"
    assert suggestions[0].reasoning == "integer counts"
    assert suggestions[0].alternatives == ["unit_sale_price"]


def test_suggester_nulls_a_non_catalog_target_and_drops_invented_alternatives() -> None:
    suggester = GeminiSuggester("a-project", "a-location")
    suggester._call_model = lambda prompt: json.dumps(  # type: ignore[method-assign]
        {
            "suggestions": [
                {
                    "source_column": "qty",
                    "suggested_target": "totally_made_up_field",
                    "confidence": 0.99,
                    "alternatives": ["also_fake", "quantity"],
                }
            ]
        }
    )
    source, _model, suggestions = anyio.run(suggester.suggest, _COLUMNS, CATALOG)
    assert source == "llm"
    # GUARDRAIL: invented target nulled; only the real catalog key survives in alternatives.
    assert suggestions[0].suggested_target is None
    assert suggestions[0].alternatives == ["quantity"]


def test_suggester_falls_back_when_the_model_errors() -> None:
    suggester = GeminiSuggester("a-project", "a-location")

    def _boom(prompt: str) -> str:
        raise RuntimeError("model exploded")

    suggester._call_model = _boom  # type: ignore[method-assign]
    source, model, suggestions = anyio.run(suggester.suggest, _COLUMNS, CATALOG)
    assert source == "fallback"
    assert model is None
    assert suggestions[0].suggested_target in CATALOG_KEYS
