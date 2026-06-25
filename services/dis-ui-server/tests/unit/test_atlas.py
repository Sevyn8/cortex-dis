"""Atlas console BFF handler tests (A4 PR1), deterministic, no DB.

The app is the production ``create_app`` factory (the atlas router is mounted in
api.py); the lifespan builds an in-memory draft store and an UNCONFIGURED proposer
(no Vertex env -> profiler-only drafts, fully deterministic). Tests override
``app.state.atlas_proposer`` only where they exercise the LLM path. Super-admin
tokens come from the shared ``mint_token`` fixture.

The ratify-gate test is paramount: publish is BLOCKED (422, draft stays draft) while
any curated-bearing field is still origin: inferred (including a natural-key member),
and SUCCEEDS only after the curated layer is flipped to human and the natural key is
human-set.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dis_ui_server.atlas import InMemoryDraftStore
from dis_ui_server.infer.proposer import FieldProposer
from dis_ui_server.main import create_app
from dis_ui_server.suggest.vertex_client import VertexClient

TokenMinter = Callable[..., str]

_API = "/api/v1"
_CSV = b"sku_id,price,name\nA1,10.50,Apple\nB2,20.00,Banana\nC3,30.25,Cherry\n"


@pytest.fixture
def atlas_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@127.0.0.1:9/ithina_dis_db")
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "ithina-bronze-raw")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:9")
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:9")
    app = create_app()
    with TestClient(app) as client:
        # Keep the no-DB handler tests on the in-memory double: production now wires
        # PostgresDraftStore (PR2), so these tests inject the in-memory store the same
        # way they inject the fake proposer. Assertions are unchanged from PR1.
        cast(FastAPI, client.app).state.atlas_store = InMemoryDraftStore()
        yield client


def _admin(mint_token: TokenMinter) -> dict[str, str]:
    token = mint_token(user_type="PLATFORM", tenant_id=None, roles=("atlas:schema:publish",))
    return {"Authorization": f"Bearer {token}"}


def _create_draft(client: TestClient, headers: dict[str, str]) -> str:
    resp = client.post(
        f"{_API}/atlas/verticals/retail/draft",
        files=[("files", ("posdump.csv", _CSV, "text/csv"))],
        data={"table_key": "store_sku_current_position"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return cast(str, resp.json()["draft_id"])


# --- (2) super-admin gate ----------------------------------------------------------


def test_super_admin_gate_blocks_non_super_admin(atlas_client: TestClient, mint_token: TokenMinter) -> None:
    ops_token = mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:ops", "dis:read"))
    headers = {"Authorization": f"Bearer {ops_token}"}
    cases = [
        atlas_client.get(f"{_API}/atlas/drafts/some-id", headers=headers),
        atlas_client.patch(f"{_API}/atlas/drafts/some-id", headers=headers, json={}),
        atlas_client.post(f"{_API}/atlas/drafts/some-id/publish", headers=headers),
        atlas_client.post(
            f"{_API}/atlas/verticals/retail/draft",
            files=[("files", ("posdump.csv", _CSV, "text/csv"))],
            headers=headers,
        ),
    ]
    for resp in cases:
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "super_admin_required"


# --- (1) the ratify gate (paramount) -----------------------------------------------


def test_ratify_gate_blocks_publish_until_curated_layer_is_human(
    atlas_client: TestClient, mint_token: TokenMinter
) -> None:
    headers = _admin(mint_token)
    draft_id = _create_draft(atlas_client, headers)

    # A fresh draft is a merge_upsert table with no ratified natural key -> publish blocked.
    blocked = atlas_client.post(f"{_API}/atlas/drafts/{draft_id}/publish", headers=headers)
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "draft_not_ratified"
    # The draft stays a draft.
    assert atlas_client.get(f"{_API}/atlas/drafts/{draft_id}", headers=headers).json()["status"] == "draft"

    # Ratify: set the natural key AND flip its member field to human.
    patched = atlas_client.patch(
        f"{_API}/atlas/drafts/{draft_id}",
        json={"natural_key": ["sku_id"], "fields": [{"name": "sku_id", "origin": "human"}]},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text

    # Now publishable: freeze succeeds, status flips to published.
    published = atlas_client.post(f"{_API}/atlas/drafts/{draft_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["status"] == "published"
    assert (
        atlas_client.get(f"{_API}/atlas/drafts/{draft_id}", headers=headers).json()["status"] == "published"
    )


# --- (4) natural key set but members unratified --------------------------------------


def test_natural_key_set_but_members_still_inferred_is_rejected(
    atlas_client: TestClient, mint_token: TokenMinter
) -> None:
    headers = _admin(mint_token)
    draft_id = _create_draft(atlas_client, headers)

    # natural_key looks set, but the member field sku_id is still origin: inferred.
    atlas_client.patch(
        f"{_API}/atlas/drafts/{draft_id}",
        json={"natural_key": ["sku_id"]},  # no origin flip
        headers=headers,
    )
    resp = atlas_client.post(f"{_API}/atlas/drafts/{draft_id}/publish", headers=headers)
    assert resp.status_code == 422
    violations = resp.json()["error"]["details"]["violations"]
    assert any("sku_id" in v for v in violations), violations
    # Still unpublished.
    assert atlas_client.get(f"{_API}/atlas/drafts/{draft_id}", headers=headers).json()["status"] == "draft"


# --- (3) lifecycle: patch reflected in get; degrade; update-after-publish ------------


def test_patch_edits_are_reflected_in_get(atlas_client: TestClient, mint_token: TokenMinter) -> None:
    headers = _admin(mint_token)
    draft_id = _create_draft(atlas_client, headers)
    # A fresh business field is origin inferred and not curated-bearing.
    fresh = atlas_client.get(f"{_API}/atlas/drafts/{draft_id}", headers=headers).json()
    sku = next(f for f in fresh["table"]["fields"] if f["name"] == "sku_id")
    assert sku["origin"] == "inferred" and sku["curated_bearing"] is False

    atlas_client.patch(
        f"{_API}/atlas/drafts/{draft_id}",
        json={"fields": [{"name": "sku_id", "mandatory": True, "origin": "human"}]},
        headers=headers,
    )
    after = atlas_client.get(f"{_API}/atlas/drafts/{draft_id}", headers=headers).json()
    sku2 = next(f for f in after["table"]["fields"] if f["name"] == "sku_id")
    assert sku2["mandatory"] is True and sku2["origin"] == "human"
    assert sku2["curated_bearing"] is True  # mandatory now makes it curated-bearing


def test_system_field_edit_is_rejected(atlas_client: TestClient, mint_token: TokenMinter) -> None:
    headers = _admin(mint_token)
    draft_id = _create_draft(atlas_client, headers)
    resp = atlas_client.patch(
        f"{_API}/atlas/drafts/{draft_id}",
        json={"fields": [{"name": "tenant_id", "nullable": True}]},  # locked system field
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "draft_state_conflict"


def test_model_failure_degrades_to_profiler_only_draft(
    atlas_client: TestClient, mint_token: TokenMinter
) -> None:
    # A CONFIGURED proposer whose model call raises -> propose degrades to no proposals
    # and the draft still assembles from the profiler alone (never raises).
    proposer = FieldProposer(VertexClient("a-project", "a-location", model="gemini-2.5-flash"))

    def _boom(prompt: str) -> str:
        raise RuntimeError("model exploded")

    proposer._call_model = _boom  # type: ignore[method-assign]
    cast(FastAPI, atlas_client.app).state.atlas_proposer = proposer
    draft_id = _create_draft(atlas_client, _admin(mint_token))  # 200 despite the model error
    body = atlas_client.get(f"{_API}/atlas/drafts/{draft_id}", headers=_admin(mint_token)).json()
    assert {f["name"] for f in body["table"]["fields"]} >= {"sku_id", "price", "name"}


def test_update_after_publish_conflicts(atlas_client: TestClient, mint_token: TokenMinter) -> None:
    headers = _admin(mint_token)
    draft_id = _create_draft(atlas_client, headers)
    atlas_client.patch(
        f"{_API}/atlas/drafts/{draft_id}",
        json={"natural_key": ["sku_id"], "fields": [{"name": "sku_id", "origin": "human"}]},
        headers=headers,
    )
    assert atlas_client.post(f"{_API}/atlas/drafts/{draft_id}/publish", headers=headers).status_code == 200
    # Editing a published version is a 409 (immutable).
    conflict = atlas_client.patch(
        f"{_API}/atlas/drafts/{draft_id}",
        json={"fields": [{"name": "price", "mandatory": True, "origin": "human"}]},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "draft_state_conflict"


# --- (PR3a) GET /atlas/drafts registry list -----------------------------------------


def _ratify_and_publish(client: TestClient, headers: dict[str, str], draft_id: str) -> None:
    client.patch(
        f"{_API}/atlas/drafts/{draft_id}",
        json={"natural_key": ["sku_id"], "fields": [{"name": "sku_id", "origin": "human"}]},
        headers=headers,
    )
    assert client.post(f"{_API}/atlas/drafts/{draft_id}/publish", headers=headers).status_code == 200


def test_list_drafts_returns_lean_summaries(atlas_client: TestClient, mint_token: TokenMinter) -> None:
    headers = _admin(mint_token)
    a = _create_draft(atlas_client, headers)
    b = _create_draft(atlas_client, headers)
    resp = atlas_client.get(f"{_API}/atlas/drafts", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["draft_id"] for r in rows} == {a, b}
    for row in rows:
        # Lean: the registry row carries the summary keys, never the full IR document.
        assert set(row) == {
            "draft_id",
            "vertical",
            "table_key",
            "status",
            "schema_version",
            "created_at",
            "updated_at",
            "published_at",
        }
        assert "table" not in row and "fields" not in row and "ir" not in row
        assert row["vertical"] == "retail"
        assert row["table_key"] == "store_sku_current_position"
        assert row["status"] == "draft"


def test_list_status_filter_is_server_side(atlas_client: TestClient, mint_token: TokenMinter) -> None:
    headers = _admin(mint_token)
    draft_only = _create_draft(atlas_client, headers)
    published = _create_draft(atlas_client, headers)
    _ratify_and_publish(atlas_client, headers, published)

    all_rows = atlas_client.get(f"{_API}/atlas/drafts", headers=headers).json()
    assert {r["draft_id"] for r in all_rows} == {draft_only, published}  # absent = all

    draft_rows = atlas_client.get(f"{_API}/atlas/drafts?status=draft", headers=headers).json()
    assert {r["draft_id"] for r in draft_rows} == {draft_only}

    pub_rows = atlas_client.get(f"{_API}/atlas/drafts?status=published", headers=headers).json()
    assert {r["draft_id"] for r in pub_rows} == {published}
    assert pub_rows[0]["status"] == "published"

    # An unknown status is rejected by FastAPI validation (the Literal query param).
    assert atlas_client.get(f"{_API}/atlas/drafts?status=bogus", headers=headers).status_code == 422


def test_list_drafts_super_admin_gate(atlas_client: TestClient, mint_token: TokenMinter) -> None:
    ops_token = mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:ops",))
    resp = atlas_client.get(f"{_API}/atlas/drafts", headers={"Authorization": f"Bearer {ops_token}"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "super_admin_required"
