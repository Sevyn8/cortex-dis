"""``/mapping-templates`` against the live stack — RLS scoping, the trigger, the EXCLUDE.

Each test works under a scratch ``source_id`` unique to the run; teardown
deletes those rows via the admin role (BYPASSRLS — cleanup only, never the path
under test). Create now writes ACTIVE directly (Slice 16c, single state); the
remaining lifecycle states the create path does NOT produce (STAGED, DEPRECATED,
multi-version heads) are still staged by direct admin UPDATE / seed, simulating
the future promote slice's output.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from dis_core.ids import new_uuid7
from dis_mapping import SourceMapping
from dis_ui_server.main import create_app

pytestmark = pytest.mark.integration

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"
TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sale_rules(constant_currency: str = "EUR") -> dict[str, Any]:
    """A complete valid sale template (the unit-test shape, full mandatory coverage)."""
    return {
        "version": 1,
        "rename": {
            "item_code": "sku_id",
            "qty": "quantity",
            "price": "unit_retail_price",
            "paid": "unit_sale_price",
            "ts": "source_sale_timestamp",
            "kind": "event_subtype",
        },
        "normalize": {
            "quantity": [
                {"op": "parse_decimal", "args": {"decimal_separator": ".", "thousands_separator": None}}
            ],
            "source_sale_timestamp": [
                {"op": "parse_datetime", "args": {"format": "%Y-%m-%d %H:%M:%S", "timezone": "UTC"}}
            ],
        },
        "cast": {
            "quantity": {"type": "decimal", "precision": 14, "scale": 3},
            "source_sale_timestamp": {"type": "datetime"},
        },
        "derive": {
            "event_date": [{"op": "date_from_datetime", "args": {"source_column": "source_sale_timestamp"}}],
            "currency": [{"op": "constant", "args": {"value": constant_currency}}],
        },
    }


def _canonical(rules: dict[str, Any]) -> dict[str, Any]:
    """What create/edit stores and reads serve: the VALIDATED model's dump (the
    canonical normalized form — e.g. non-decimal casts carry explicit null
    precision/scale), not the request's surface syntax."""
    return SourceMapping.model_validate(rules).model_dump(mode="json")


@pytest.fixture
def live_client(stack_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("POSTGRES_URL", stack_env["POSTGRES_URL"])
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def admin_engine(stack_env: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(stack_env["POSTGRES_ADMIN_URL"])
    yield engine
    engine.dispose()


@pytest.fixture
def scratch_source(admin_engine: Engine) -> Iterator[str]:
    """A run-unique source_id; rows under it are admin-deleted at teardown."""
    source_id = f"t14b_{new_uuid7().hex[:12]}"
    yield source_id
    with admin_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM config.source_mappings WHERE source_id = :sid"),
            {"sid": source_id},
        )


def _snapshot_columns() -> list[dict[str, Any]]:
    """A complete valid SNAPSHOT create body (columns contract, Slice 16a/c): exactly the
    mandatory mapping-produced snapshot columns, so the gate passes with no derive (the
    columns contract expresses none). No promo/expiry column, so no presence pairing fires."""
    return [
        {"src_key": "codice", "dest_key": "sku_id"},
        {"src_key": "nome", "dest_key": "product_name"},
        {"src_key": "categoria", "dest_key": "product_category"},
        {"src_key": "prezzo", "dest_key": "current_retail_price", "src_decimal_separator": "."},
        {"src_key": "costo", "dest_key": "unit_cost", "src_decimal_separator": "."},
        {"src_key": "valuta", "dest_key": "currency"},
    ]


def _expected_snapshot_translation() -> dict[str, Any]:
    """The engine ``mapping_rules`` a ``_snapshot_columns()`` body should translate to — an
    INDEPENDENT oracle, hand-written (not the translator's own output). ``_canonical()``
    normalizes it the same way the handler stores it (validated model dump: non-decimal
    casts gain explicit null precision/scale), so the stored row must round-trip to it."""
    return {
        "version": 1,
        "rename": {
            "codice": "sku_id",
            "nome": "product_name",
            "categoria": "product_category",
            "prezzo": "current_retail_price",
            "costo": "unit_cost",
            "valuta": "currency",
        },
        "normalize": {
            "current_retail_price": [
                {"op": "parse_decimal", "args": {"decimal_separator": ".", "thousands_separator": None}}
            ],
            "unit_cost": [
                {"op": "parse_decimal", "args": {"decimal_separator": ".", "thousands_separator": None}}
            ],
        },
        "cast": {
            "sku_id": {"type": "string"},
            "product_name": {"type": "string"},
            "product_category": {"type": "string"},
            "current_retail_price": {"type": "decimal", "precision": 12, "scale": 4},
            "unit_cost": {"type": "decimal", "precision": 12, "scale": 4},
            "currency": {"type": "string"},
        },
        "derive": {},
    }


def _create(
    client: TestClient,
    mint_token: Callable[..., str],
    source_id: str,
    *,
    template_name: str = "catalogue",
    template_type: str = "snapshot",
    tenant_id: str = TENANT_A,
) -> Any:
    return client.post(
        "/api/v1/mapping-templates",
        headers=_bearer(mint_token(tenant_id=tenant_id)),
        json={
            "source_id": source_id,
            "template_name": template_name,
            "template_type": template_type,
            "columns": _snapshot_columns(),
        },
    )


def _seed_draft(
    admin_engine: Engine,
    source_id: str,
    *,
    template_name: str = "sales",
    template_type: str = "sales",
    tenant_id: str = TENANT_A,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Seed a DRAFT row directly via the admin role (superuser, bypasses RLS) — the
    16a stand-in for the create endpoint, which no longer persists (persistence
    returns in 16c). Mirrors the columns ``repos.create_template`` sets; the
    BEFORE-INSERT trigger assigns ``version_seq_per_source`` and the BIGSERIAL
    assigns ``mapping_version_id``. Returns the minimal shape the read/patch tests
    consumed from the old create response: ``{template_id, versions:[{mapping_version_id}]}``.
    """
    template_id = new_uuid7()
    stored = SourceMapping.model_validate(rules or _sale_rules()).model_dump(mode="json")
    with admin_engine.begin() as conn:
        mapping_version_id = conn.execute(
            text(
                "INSERT INTO config.source_mappings "
                "(tenant_id, source_id, template_id, template_name, template_type, status, mapping_rules) "
                "VALUES (:ten, :src, :tid, :name, :type, 'DRAFT', CAST(:rules AS jsonb)) "
                "RETURNING mapping_version_id"
            ),
            {
                "ten": tenant_id,
                "src": source_id,
                "tid": str(template_id),
                "name": template_name,
                "type": template_type,
                "rules": json.dumps(stored),
            },
        ).scalar_one()
    return {"template_id": str(template_id), "versions": [{"mapping_version_id": mapping_version_id}]}


def _force_status(admin_engine: Engine, mapping_version_id: int, status: str) -> None:
    """Stage a post-promote lifecycle state (the future slice's output) for a test."""
    timestamps = {
        "ACTIVE": "activated_at = NOW(), deprecated_at = NULL",
        "DEPRECATED": "activated_at = COALESCE(activated_at, NOW()), deprecated_at = NOW()",
    }[status]
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE config.source_mappings SET status = :status, {timestamps} "  # noqa: S608
                "WHERE mapping_version_id = :mvid"
            ),
            {"status": status, "mvid": mapping_version_id},
        )


# -- create (d) — Slice 16c: translate -> validate -> ACTIVE write -------------------


def test_create_writes_a_valid_active_row_with_a_minted_uuid7(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    response = _create(live_client, mint_token, scratch_source)
    assert response.status_code == 201, response.text
    body = response.json()

    assert UUID(body["template_id"]).version == 7  # minted server-side, UUIDv7 (hard rule 3)
    assert body["source_id"] == scratch_source
    assert body["template_name"] == "catalogue"
    assert body["template_type"] == "snapshot"  # captured + surfaced (Slice 14d)
    assert body["latest_version"] == 1  # trigger-assigned, lineage starts at 1
    # Single state (Slice 16c): create is ACTIVE, not DRAFT.
    assert body["active_version"] == 1
    assert body["draft_version"] is None and body["staged_version"] is None
    assert body["versions_count"] == 1

    (version,) = body["versions"]
    assert version["status"] == "active"
    assert version["version"] == 1
    assert version["mapping_version_id"] > 0  # real BIGSERIAL, not the 16a 0 sentinel
    assert version["predecessor_version_id"] is None
    assert version["activated_at"] is not None  # stamped on the ACTIVE write
    # The translated + validated document round-trips (the independent-oracle translation).
    assert version["mapping_rules"] == _canonical(_expected_snapshot_translation())
    assert version["field_count"] == 6
    assert version["transform_count"] == 8  # 2 normalize ops + 6 casts + 0 derive

    # The stored row, off the database directly (admin read): ACTIVE, activated, seq 1, tenant A.
    with admin_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT tenant_id, status, version_seq_per_source, predecessor_version_id, "
                "template_type, activated_at FROM config.source_mappings WHERE source_id = :sid"
            ),
            {"sid": scratch_source},
        ).one()
    assert str(row.tenant_id) == TENANT_A
    assert row.status == "ACTIVE"
    assert row.activated_at is not None  # satisfies ck_csm_activated_at
    assert row.version_seq_per_source == 1
    assert row.predecessor_version_id is None
    assert row.template_type == "snapshot"  # stored, not inferred (Slice 14d)


def test_duplicate_template_name_is_a_clean_409(
    live_client: TestClient, mint_token: Callable[..., str], scratch_source: str
) -> None:
    assert _create(live_client, mint_token, scratch_source).status_code == 201
    duplicate = _create(live_client, mint_token, scratch_source)  # same name, same source
    assert duplicate.status_code == 409, duplicate.text
    envelope = duplicate.json()["error"]
    assert envelope["code"] == "mapping_template_name_conflict"
    assert envelope["details"]["source_id"] == scratch_source
    assert envelope["details"]["template_name"] == "catalogue"
    # A second template under the SAME source with a DIFFERENT name is fine (D68 grain)...
    assert _create(live_client, mint_token, scratch_source, template_name="inventory").status_code == 201


def test_same_name_under_another_source_is_fine(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    other_source = f"{scratch_source}_b"
    try:
        assert _create(live_client, mint_token, scratch_source).status_code == 201
        assert _create(live_client, mint_token, other_source).status_code == 201
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM config.source_mappings WHERE source_id = :sid"),
                {"sid": other_source},
            )


def test_create_persists_nothing_on_a_rejected_request(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    """The Slice 16c no-write-on-failure guarantee, asserted DIRECTLY against the live DB
    (the inverse of the old 16a no-write test): a request the semantic gate REJECTS — here
    an incomplete snapshot missing mandatory mapping-produced columns — is a 4xx and writes
    ZERO rows. The gate runs before any ``rls_session``, so nothing reaches the DB."""
    response = live_client.post(
        "/api/v1/mapping-templates",
        headers=_bearer(mint_token(tenant_id=TENANT_A)),
        json={
            "source_id": scratch_source,
            "template_name": "catalogue",
            "template_type": "snapshot",
            "columns": [{"src_key": "item", "dest_key": "sku_id"}],  # missing mandatory columns
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "mapping_config"
    with admin_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM config.source_mappings WHERE source_id = :sid"),
            {"sid": scratch_source},
        ).scalar_one()
    assert count == 0, f"a rejected create must persist nothing; found {count} row(s)"


def test_bad_dest_key_persists_nothing(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    """A SECOND live-DB no-write pin (beyond missing-mandatory): a request rejected for an
    illegal dest_key (target legality) is a 4xx and writes ZERO rows. So the no-write-on-
    failure guarantee is carried by a live-DB count for two distinct rejection paths, not by
    DB-unreachable inference alone (closes the inventory's coverage gap)."""
    response = live_client.post(
        "/api/v1/mapping-templates",
        headers=_bearer(mint_token(tenant_id=TENANT_A)),
        json={
            "source_id": scratch_source,
            "template_name": "catalogue",
            "template_type": "snapshot",
            # all mandatory present, plus one ILLEGAL target -> check_target_legality 400
            "columns": [*_snapshot_columns(), {"src_key": "bogus", "dest_key": "not_a_real_column"}],
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "mapping_config"
    with admin_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM config.source_mappings WHERE source_id = :sid"),
            {"sid": scratch_source},
        ).scalar_one()
    assert count == 0, f"a target-legality rejection must persist nothing; found {count} row(s)"


# -- reads (c) — RLS isolation -------------------------------------------------------


def test_templates_are_invisible_across_tenants(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    template_id = _seed_draft(admin_engine, scratch_source)["template_id"]

    # Owner: list (filtered) shows it, detail serves it.
    own_list = live_client.get(
        f"/api/v1/mapping-templates?source_id={scratch_source}",
        headers=_bearer(mint_token(tenant_id=TENANT_A)),
    )
    assert [t["template_id"] for t in own_list.json()] == [template_id]
    assert (
        live_client.get(
            f"/api/v1/mapping-templates/{template_id}",
            headers=_bearer(mint_token(tenant_id=TENANT_A)),
        ).status_code
        == 200
    )

    # Tenant B: the same template does not exist — empty list, 404 detail, 404 PATCH.
    b_headers = _bearer(mint_token(tenant_id=TENANT_B))
    assert (
        live_client.get(f"/api/v1/mapping-templates?source_id={scratch_source}", headers=b_headers).json()
        == []
    )
    detail = live_client.get(f"/api/v1/mapping-templates/{template_id}", headers=b_headers)
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "resource_not_found"
    patch = live_client.patch(
        f"/api/v1/mapping-templates/{template_id}",
        headers=b_headers,
        json={"template_name": "stolen"},
    )
    assert patch.status_code == 404


def test_unknown_template_is_404(live_client: TestClient, mint_token: Callable[..., str]) -> None:
    response = live_client.get(
        f"/api/v1/mapping-templates/{new_uuid7()}",
        headers=_bearer(mint_token(tenant_id=TENANT_A)),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_non_uuid_token_sub_creates_active_with_null_created_by(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    """A verified token whose ``sub`` is not a UUID still creates an ACTIVE row (201); the
    authorship column is NULL by design (handlers/mapping_templates.py ``_created_by_uuid``
    — the claim vocabulary is unsigned, D56/Blocker 5), on the wire AND on the written row."""
    response = live_client.post(
        "/api/v1/mapping-templates",
        headers=_bearer(mint_token(tenant_id=TENANT_A, sub="svc-account-7")),  # not a UUID
        json={
            "source_id": scratch_source,
            "template_name": "catalogue",
            "template_type": "snapshot",
            "columns": _snapshot_columns(),
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["versions"][0]["status"] == "active"
    assert response.json()["versions"][0]["created_by_user_id"] is None  # the wire shape

    with admin_engine.begin() as conn:  # and the written row itself
        stored = conn.execute(
            text("SELECT created_by_user_id, status FROM config.source_mappings WHERE source_id = :sid"),
            {"sid": scratch_source},
        ).one()
    assert stored.created_by_user_id is None
    assert stored.status == "ACTIVE"


# -- edit (e) — the D17 lifecycle ------------------------------------------------------


def test_patch_edits_a_draft_in_place(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    created = _seed_draft(admin_engine, scratch_source)
    template_id = created["template_id"]
    original_mvid = created["versions"][0]["mapping_version_id"]

    response = live_client.patch(
        f"/api/v1/mapping-templates/{template_id}",
        headers=_bearer(mint_token(tenant_id=TENANT_A)),
        json={"mapping_rules": _sale_rules(constant_currency="GBP")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # In place: no new version, same surrogate id, same seq, rules replaced.
    assert body["versions_count"] == 1
    (version,) = body["versions"]
    assert version["mapping_version_id"] == original_mvid
    assert version["version"] == 1
    assert version["status"] == "draft"
    assert version["mapping_rules"]["derive"]["currency"][0]["args"]["value"] == "GBP"


def test_patch_on_an_active_head_chains_a_new_draft(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    created = _seed_draft(admin_engine, scratch_source)
    template_id = created["template_id"]
    active_mvid = created["versions"][0]["mapping_version_id"]
    _force_status(admin_engine, active_mvid, "ACTIVE")  # the future promote slice's output

    response = live_client.patch(
        f"/api/v1/mapping-templates/{template_id}",
        headers=_bearer(mint_token(tenant_id=TENANT_A)),
        json={"mapping_rules": _sale_rules(constant_currency="GBP")},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # A NEW version: DRAFT, next seq, chained to the immutable head.
    assert body["versions_count"] == 2
    assert body["active_version"] == 1
    assert body["draft_version"] == 2
    draft, active = body["versions"]  # version desc
    assert draft["status"] == "draft" and draft["version"] == 2
    assert draft["predecessor_version_id"] == active_mvid
    assert draft["mapping_rules"]["derive"]["currency"][0]["args"]["value"] == "GBP"
    # The ACTIVE version is byte-unchanged (D17 immutability) — and still the only ACTIVE.
    assert active["mapping_version_id"] == active_mvid
    assert active["status"] == "active"
    assert active["mapping_rules"] == _canonical(_sale_rules())
    assert sum(1 for v in body["versions"] if v["status"] == "active") == 1

    # A further rules PATCH reuses the one DRAFT (write-path convention) — never a second.
    again = live_client.patch(
        f"/api/v1/mapping-templates/{template_id}",
        headers=_bearer(mint_token(tenant_id=TENANT_A)),
        json={"mapping_rules": _sale_rules(constant_currency="USD")},
    )
    assert again.json()["versions_count"] == 2


def test_patch_on_a_fully_deprecated_lineage_is_409(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    created = _seed_draft(admin_engine, scratch_source)
    _force_status(admin_engine, created["versions"][0]["mapping_version_id"], "DEPRECATED")

    response = live_client.patch(
        f"/api/v1/mapping-templates/{created['template_id']}",
        headers=_bearer(mint_token(tenant_id=TENANT_A)),
        json={"mapping_rules": _sale_rules()},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mapping_state_conflict"


def test_patch_with_invalid_rules_is_400_and_writes_nothing(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    """PATCH validates ``mapping_rules`` through the same four-step gate as POST
    BEFORE any write (handlers/mapping_templates.py): bad rules are a 400
    ``mapping_config``, and the lineage is byte-identical afterwards — no new
    DRAFT chained, no in-place edit."""
    headers = _bearer(mint_token(tenant_id=TENANT_A))
    template_id = _seed_draft(admin_engine, scratch_source)["template_id"]

    bad_rules = {"version": 1, "rename": {}, "normalize": {}, "cast": {}, "derive": {}}  # empty rename
    patched = live_client.patch(
        f"/api/v1/mapping-templates/{template_id}",
        headers=headers,
        json={"mapping_rules": bad_rules},
    )
    assert patched.status_code == 400
    assert patched.json()["error"]["code"] == "mapping_config"

    detail = live_client.get(f"/api/v1/mapping-templates/{template_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["versions_count"] == 1, "no new DRAFT was chained by the rejected PATCH"
    assert body["draft_version"] == 1
    assert body["versions"][0]["mapping_rules"] == _canonical(_sale_rules()), "rules unchanged"


def test_rename_updates_the_whole_lineage_and_conflicts_cleanly(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    headers = _bearer(mint_token(tenant_id=TENANT_A))
    first = _seed_draft(admin_engine, scratch_source, template_name="sales")
    _seed_draft(admin_engine, scratch_source, template_name="inventory")

    # Give the first template a two-row lineage (ACTIVE head + chained DRAFT).
    _force_status(admin_engine, first["versions"][0]["mapping_version_id"], "ACTIVE")
    live_client.patch(
        f"/api/v1/mapping-templates/{first['template_id']}",
        headers=headers,
        json={"mapping_rules": _sale_rules(constant_currency="GBP")},
    )

    renamed = live_client.patch(
        f"/api/v1/mapping-templates/{first['template_id']}",
        headers=headers,
        json={"template_name": "sales-eu"},
    )
    assert renamed.status_code == 200
    body = renamed.json()
    assert body["template_name"] == "sales-eu"
    assert body["versions_count"] == 2  # a rename mints NO version (lineage metadata)
    with admin_engine.connect() as conn:
        names = (
            conn.execute(
                text("SELECT DISTINCT template_name FROM config.source_mappings WHERE template_id = :tid"),
                {"tid": first["template_id"]},
            )
            .scalars()
            .all()
        )
    assert names == ["sales-eu"]  # every lineage row, coherently

    # Renaming onto the sibling template's name: the EXCLUDE constraint, as a 409.
    conflict = live_client.patch(
        f"/api/v1/mapping-templates/{first['template_id']}",
        headers=headers,
        json={"template_name": "inventory"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "mapping_template_name_conflict"


def test_concurrent_patches_converge_to_one_draft(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    """The lock-then-reread proof (found in the 14b validation pass).

    Deterministic interleaving: txn1 holds the lineage FOR UPDATE, inserts a
    DRAFT v2, commits; txn2's patch_template was blocked behind it the whole
    time. Under a single locked read txn2 resumes on a stale statement snapshot
    (no v2), takes the new-version branch, and the seq trigger — running on a
    FRESH snapshot — assigns seq 3 without tripping the unique backstop: a
    double-DRAFT (reproduced live before the fix). The re-read makes txn2 see
    v2 and edit it in place.
    """
    import asyncio
    import json as jsonlib

    from dis_rls import create_rls_engine, rls_session
    from dis_ui_server.auth.identity import UserType
    from dis_ui_server.auth.scope import ReadScope
    from dis_ui_server.repos.mapping_templates import get_template_rows, patch_template

    created = _seed_draft(admin_engine, scratch_source)
    template_id = UUID(created["template_id"])
    active_mvid = created["versions"][0]["mapping_version_id"]
    _force_status(admin_engine, active_mvid, "ACTIVE")

    tenant = UUID(TENANT_A)
    concurrent_rules = _sale_rules(constant_currency="GBP")
    patched_rules = _sale_rules(constant_currency="USD")

    async def _race() -> list[Any]:
        engine = create_rls_engine()
        try:

            async def txn1() -> None:
                async with rls_session(engine, tenant) as conn:
                    await conn.execute(
                        text("SELECT * FROM config.source_mappings WHERE template_id = :t FOR UPDATE"),
                        {"t": str(template_id)},
                    )
                    await asyncio.sleep(1.0)  # txn2 is now blocked behind the lock
                    await conn.execute(
                        text(
                            "INSERT INTO config.source_mappings (tenant_id, source_id, template_id, "
                            "template_name, template_type, status, mapping_rules, predecessor_version_id) "
                            "VALUES (:ten, :src, :t, 'sales', 'sales', 'DRAFT', CAST(:r AS jsonb), :p)"
                        ),
                        {
                            "ten": str(tenant),
                            "src": scratch_source,
                            "t": str(template_id),
                            "r": jsonlib.dumps(concurrent_rules),
                            "p": active_mvid,
                        },
                    )
                # context exit commits the DRAFT v2

            async def txn2() -> None:
                await asyncio.sleep(0.3)  # start while txn1 holds the lock
                await patch_template(
                    engine,
                    tenant,
                    template_id,
                    template_name=None,
                    mapping_rules=patched_rules,
                    created_by_user_id=None,
                    user_type=UserType.TENANT,
                )

            await asyncio.gather(txn1(), txn2())
            return list(
                await get_template_rows(engine, ReadScope(is_platform=False, tenant_id=tenant), template_id)
            )
        finally:
            await engine.dispose()

    rows = asyncio.run(_race())
    drafts = [row for row in rows if row.status == "DRAFT"]
    assert len(rows) == 2, [(r.version_seq_per_source, r.status) for r in rows]
    assert len(drafts) == 1  # NOT a double-DRAFT
    (draft,) = drafts
    assert draft.version_seq_per_source == 2
    assert draft.predecessor_version_id == active_mvid  # chain stays coherent
    # txn2 edited txn1's committed DRAFT in place (branch 3), not a new version.
    assert draft.mapping_rules["derive"]["currency"][0]["args"]["value"] == "USD"


def test_rename_plus_rules_conflict_rolls_back_together(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    """One PATCH carrying BOTH a colliding rename AND new rules: 409, and the
    transaction leaves NOTHING behind — neither the rename nor the rules edit."""
    headers = _bearer(mint_token(tenant_id=TENANT_A))
    first = _seed_draft(admin_engine, scratch_source, template_name="sales")
    _seed_draft(admin_engine, scratch_source, template_name="inventory")

    response = live_client.patch(
        f"/api/v1/mapping-templates/{first['template_id']}",
        headers=headers,
        json={
            "template_name": "inventory",  # collides on the EXCLUDE
            "mapping_rules": _sale_rules(constant_currency="GBP"),
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mapping_template_name_conflict"

    detail = live_client.get(f"/api/v1/mapping-templates/{first['template_id']}", headers=headers).json()
    assert detail["template_name"] == "sales"  # rename rolled back
    assert detail["versions_count"] == 1  # no rules edit landed
    rules = detail["versions"][0]["mapping_rules"]
    assert rules["derive"]["currency"][0]["args"]["value"] == "EUR"  # original rules intact


def test_well_formed_unknown_tenant_reads_empty_writes_403(
    live_client: TestClient, mint_token: Callable[..., str], scratch_source: str
) -> None:
    """A verified token whose tenant_id is a well-formed UUID DIS never mirrored:
    reads are indistinguishable from 'tenant with no data' (no oracle), and the
    one place the difference is detectable — the tenant FK on the ACTIVE create write
    — is a clean 403, not a 500."""
    ghost = str(new_uuid7())
    headers = _bearer(mint_token(tenant_id=ghost))

    assert live_client.get("/api/v1/mapping-templates", headers=headers).json() == []
    detail = live_client.get(f"/api/v1/mapping-templates/{new_uuid7()}", headers=headers)
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "resource_not_found"  # same 404 as no-data tenants

    create = live_client.post(
        "/api/v1/mapping-templates",
        headers=headers,
        json={
            "source_id": scratch_source,
            "template_name": "ghost",
            "template_type": "snapshot",
            "columns": _snapshot_columns(),
        },
    )
    assert create.status_code == 403, create.text
    body = create.json()["error"]
    assert body["code"] == "tenant_scope"
    assert "not provisioned" in body["message"]


def test_no_endpoint_can_mint_active_or_staged(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
    admin_engine: Engine,
) -> None:
    # The 14a consumer `.first()` hazard guard: across a seeded DRAFT + an in-place
    # edit + a rename, every stored row is still DRAFT (the PATCH/rename paths never
    # change a row's status; create-as-ACTIVE (Slice 16c) is a separate path, not
    # exercised here — STAGED and the promote/deprecate transitions remain unbuilt).
    headers = _bearer(mint_token(tenant_id=TENANT_A))
    template_id = _seed_draft(admin_engine, scratch_source)["template_id"]
    live_client.patch(
        f"/api/v1/mapping-templates/{template_id}",
        headers=headers,
        json={"mapping_rules": _sale_rules(constant_currency="USD"), "template_name": "renamed"},
    )
    detail = live_client.get(f"/api/v1/mapping-templates/{template_id}", headers=headers).json()
    assert {v["status"] for v in detail["versions"]} == {"draft"}


# -- Slice 17b: PLATFORM impersonation write + the reject paths, over the real HTTP path ---


def _impersonation_body(source_id: str, *, acting_for: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "source_id": source_id,
        "template_name": f"impersonation-{source_id}",
        "template_type": "snapshot",
        "columns": _snapshot_columns(),
    }
    if acting_for is not None:
        body["acting_for_tenant_id"] = acting_for
    return body


def test_platform_impersonation_post_and_patch_target_tenant_a(
    live_client: TestClient,
    mint_token: Callable[..., str],
    admin_engine: Engine,
    scratch_source: str,
) -> None:
    # PLATFORM+dis:ops with acting_for_tenant_id=A: POST then PATCH land for tenant A, and the
    # PERSISTED row's tenant_id IS A (admin read). Proves require_write_scope ->
    # resolve_acted_for -> write_session -> rls_platform_session over HTTP. The handler only
    # ever writes the RESOLVED acted-for tenant, so an impersonation can affect no other
    # tenant's rows; the WITH CHECK cross-tenant-write refusal is proven at the DB layer in
    # tests/integration/test_migration_0011.py.
    platform = _bearer(mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:ops", "dis:read")))

    created = live_client.post(
        "/api/v1/mapping-templates",
        headers=platform,
        json=_impersonation_body(scratch_source, acting_for=TENANT_A),
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["template_id"]

    patched = live_client.patch(
        f"/api/v1/mapping-templates/{template_id}",
        headers=platform,
        json={"template_name": "impersonation-renamed", "acting_for_tenant_id": TENANT_A},
    )
    assert patched.status_code == 200, patched.text

    with admin_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tenant_id, template_name FROM config.source_mappings WHERE source_id = :sid"),
            {"sid": scratch_source},
        ).all()
    assert rows, "impersonation POST persisted no row"
    assert all(str(r.tenant_id) == TENANT_A for r in rows), (
        f"a row landed for a tenant other than the acted-for A: {[str(r.tenant_id) for r in rows]}"
    )
    assert any(r.template_name == "impersonation-renamed" for r in rows), (
        "impersonation PATCH did not land for A"
    )


def test_tenant_token_carrying_acting_for_is_rejected(
    live_client: TestClient,
    mint_token: Callable[..., str],
    admin_engine: Engine,
    scratch_source: str,
) -> None:
    # Criterion 9 over HTTP: a TENANT request that names an acted-for tenant is REJECTED
    # (403), never silently ignored -- and nothing persists.
    token = _bearer(mint_token(tenant_id=TENANT_A))  # default user_type=TENANT
    resp = live_client.post(
        "/api/v1/mapping-templates",
        headers=token,
        json=_impersonation_body(scratch_source, acting_for=TENANT_B),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "tenant_scope"
    with admin_engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM config.source_mappings WHERE source_id = :sid"),
            {"sid": scratch_source},
        ).scalar_one()
    assert n == 0, "a rejected TENANT-acting-for request still persisted a row"


def test_platform_write_without_acting_for_is_rejected(
    live_client: TestClient,
    mint_token: Callable[..., str],
    scratch_source: str,
) -> None:
    # A PLATFORM write must NAME its acted-for tenant; omitting it is a clean 403 at the door.
    platform = _bearer(mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:ops",)))
    resp = live_client.post(
        "/api/v1/mapping-templates", headers=platform, json=_impersonation_body(scratch_source)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "tenant_scope"
