"""The quarantine console endpoints against the live stack (slice 15a).

``quarantine.*`` is RLS ON + FORCE (single-GUC ``tenant_isolation``, introspected
Task 0): the database backstops tenant scope, and the repo's explicit ``WHERE
tenant_id`` predicate is defense-in-depth. These tests prove both endpoints hold the
line - token A sees only A's held items, never B's, and tenant comes from the token
ONLY - plus the four filters (alone and combined, incl. resolved-returns-empty), the
filter-independent open count, and detail across both kinds (row/chunk/unknown/
malformed/cross-tenant).

15a is a READ slice - but it has no producing path of its own, so these tests seed
``quarantine.*`` directly via the ADMIN connection (superuser bypasses RLS, the
csv-uploads-live precedent) and delete exactly what they seed on teardown. Production
code writes nothing here. ``quarantined_rows`` needs a real
``config.source_mappings.mapping_version_id`` (FK, NOT NULL) - seeded for TENANT_A
(mapping_version_id=1, live). TENANT_B has no mapping, so its isolation case is a
``quarantined_chunks`` row (no mapping FK), which is also the highest-value content.
Assertions key on the SEEDED ids only, never absolute table counts, so pre-existing
rows never make them flaky.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from dis_core.ids import new_uuid7
from dis_core.timestamps import now_utc
from dis_ui_server.main import create_app

pytestmark = pytest.mark.integration

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"
TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"
TENANT_A_MAPPING_VERSION = 1  # live ACTIVE source_mappings row for TENANT_A (Task 0)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def live_client(stack_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("POSTGRES_URL", stack_env["POSTGRES_URL"])
    with TestClient(create_app()) as client:
        yield client


@dataclass(frozen=True)
class _Seed:
    """Type-tagged ids of the seeded items, plus the A open-count floor."""

    a_row_canonical: str  # TENANT_A, canonical-shape, manual_csv_upload, recent
    a_row_source: str  # TENANT_A, source-shape, manual_csv_upload, 3 days old
    a_row_fk: str  # TENANT_A, fk, sc_pos_v1, 10 days old
    a_chunk_other: str  # TENANT_A, MAPPING_LOOKUP->other, sc_pos_v1, recent, mapping_version NULL
    b_chunk: str  # TENANT_B, recent
    a_open_seeded: int  # NEW items seeded for A (open-count floor)
    a_row_uuids: tuple[str, ...]
    a_chunk_uuids: tuple[str, ...]
    b_chunk_uuids: tuple[str, ...]


_ROW_SQL = text(
    "INSERT INTO quarantine.quarantined_rows "
    "(id, tenant_id, data_ingress_event_id, trace_id, source_id, dis_channel, gcs_uri, "
    " row_offset, failure_stage, failure_reason, failure_context, mapping_version_id, quarantined_at) "
    "VALUES (:id, :tenant_id, :deid, :trace_id, :source_id, 'csv_upload', :gcs_uri, "
    " :row_offset, :failure_stage, :failure_reason, CAST(:fc AS JSONB), :mvid, :qat)"
)
_CHUNK_SQL = text(
    "INSERT INTO quarantine.quarantined_chunks "
    "(id, tenant_id, data_ingress_event_id, trace_id, source_id, dis_channel, gcs_uri, "
    " failure_stage, failure_reason, failure_context, mapping_version_id, row_count_in_chunk, "
    " quarantined_at) "
    "VALUES (:id, :tenant_id, :deid, :trace_id, :source_id, 'csv_upload', :gcs_uri, "
    " :failure_stage, :failure_reason, CAST(:fc AS JSONB), :mvid, :rcic, :qat)"
)


@pytest.fixture
def seed(stack_env: dict[str, str]) -> Iterator[_Seed]:
    base = now_utc()
    gcs = "gs://ithina-bronze-raw/tenant/x/source/s/yyyy=2026/mm=06/dd=03/x.csv"

    r_canonical, r_source, r_fk = new_uuid7(), new_uuid7(), new_uuid7()
    c_other = new_uuid7()
    b_c = new_uuid7()

    rows = [
        {
            "id": r_canonical,
            "tenant_id": TENANT_A,
            "deid": new_uuid7(),
            "trace_id": new_uuid7(),
            "source_id": "manual_csv_upload",
            "gcs_uri": gcs,
            "row_offset": 4,
            "failure_stage": "POST_MAPPING_VALIDATION",
            "failure_reason": "VALIDATION_ROW_FAILED",
            "fc": json.dumps(
                {"failures": [{"column": "price", "check": "numeric", "reason": "not a number"}]}
            ),
            "mvid": TENANT_A_MAPPING_VERSION,
            "qat": base,
        },
        {
            "id": r_source,
            "tenant_id": TENANT_A,
            "deid": new_uuid7(),
            "trace_id": new_uuid7(),
            "source_id": "manual_csv_upload",
            "gcs_uri": gcs,
            "row_offset": 1,
            "failure_stage": "PRE_MAPPING_VALIDATION",
            "failure_reason": "VALIDATION_ROW_FAILED",
            "fc": json.dumps({"failures": [{"column": "sku", "check": "required", "reason": "missing"}]}),
            "mvid": TENANT_A_MAPPING_VERSION,
            "qat": base - timedelta(days=3),
        },
        {
            "id": r_fk,
            "tenant_id": TENANT_A,
            "deid": new_uuid7(),
            "trace_id": new_uuid7(),
            "source_id": "sc_pos_v1",
            "gcs_uri": gcs,
            "row_offset": 2,
            "failure_stage": "IDENTITY_VALIDATION",
            "failure_reason": "VALIDATION_ROW_FAILED",
            "fc": json.dumps(
                {"failures": [{"column": "store_id", "check": "fk", "reason": "unknown store"}]}
            ),
            "mvid": TENANT_A_MAPPING_VERSION,
            "qat": base - timedelta(days=10),
        },
    ]
    chunks = [
        {
            "id": c_other,
            "tenant_id": TENANT_A,
            "deid": new_uuid7(),
            "trace_id": new_uuid7(),
            "source_id": "sc_pos_v1",
            "gcs_uri": gcs,
            "failure_stage": "MAPPING_LOOKUP",
            "failure_reason": "MAPPING_CONFIG_INVALID",
            "fc": json.dumps({"failure_message": "mapping config invalid"}),
            "mvid": None,
            "rcic": 12,
            "qat": base,
        },
        {
            "id": b_c,
            "tenant_id": TENANT_B,
            "deid": new_uuid7(),
            "trace_id": new_uuid7(),
            "source_id": "manual_csv_upload",
            "gcs_uri": gcs,
            "failure_stage": "OTHER",
            "failure_reason": "CONTRACT_VIOLATION",
            "fc": json.dumps({"failure_message": "contract violation"}),
            "mvid": None,
            "rcic": 3,
            "qat": base,
        },
    ]

    engine = create_engine(stack_env["POSTGRES_ADMIN_URL"])
    try:
        with engine.begin() as conn:
            for row in rows:
                conn.execute(_ROW_SQL, row)
            for chunk in chunks:
                conn.execute(_CHUNK_SQL, chunk)
        yield _Seed(
            a_row_canonical=f"row:{r_canonical}",
            a_row_source=f"row:{r_source}",
            a_row_fk=f"row:{r_fk}",
            a_chunk_other=f"chunk:{c_other}",
            b_chunk=f"chunk:{b_c}",
            a_open_seeded=4,
            a_row_uuids=(str(r_canonical), str(r_source), str(r_fk)),
            a_chunk_uuids=(str(c_other),),
            b_chunk_uuids=(str(b_c),),
        )
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM quarantine.quarantined_rows WHERE id = ANY(:ids)"),
                {"ids": [r["id"] for r in rows]},
            )
            conn.execute(
                text("DELETE FROM quarantine.quarantined_chunks WHERE id = ANY(:ids)"),
                {"ids": [c["id"] for c in chunks]},
            )
        engine.dispose()


def _list(live_client: TestClient, token: str, query: str = "") -> dict[str, object]:
    suffix = f"?{query}" if query else ""
    response = live_client.get(f"/api/v1/quarantine{suffix}", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def _ids(body: dict[str, object]) -> set[str]:
    items = body["items"]
    assert isinstance(items, list)
    return {item["id"] for item in items}


# -- tenant isolation (both endpoints, tenant from token only) ----------------------


def test_list_is_tenant_scoped(live_client: TestClient, mint_token: Callable[..., str], seed: _Seed) -> None:
    a_ids = _ids(_list(live_client, mint_token(tenant_id=TENANT_A)))
    assert {seed.a_row_canonical, seed.a_row_source, seed.a_row_fk, seed.a_chunk_other} <= a_ids
    assert seed.b_chunk not in a_ids  # A never sees B's held chunk

    b_ids = _ids(_list(live_client, mint_token(tenant_id=TENANT_B)))
    assert seed.b_chunk in b_ids
    assert a_ids.isdisjoint({seed.b_chunk}) and not (b_ids & {seed.a_row_canonical, seed.a_chunk_other})


def test_list_tenant_comes_from_token_only(
    live_client: TestClient, mint_token: Callable[..., str], seed: _Seed
) -> None:
    # A token-A request smuggling tenant B by query + header still serves only A.
    response = live_client.get(
        f"/api/v1/quarantine?tenant_id={TENANT_B}",
        headers={**_bearer(mint_token(tenant_id=TENANT_A)), "X-Tenant-Id": TENANT_B},
    )
    assert response.status_code == 200
    assert seed.b_chunk not in _ids(response.json())


def test_list_newest_first(live_client: TestClient, mint_token: Callable[..., str], seed: _Seed) -> None:
    body = _list(live_client, mint_token(tenant_id=TENANT_A))
    items = body["items"]
    assert isinstance(items, list)
    times = [item["failed_at"] for item in items]
    assert times == sorted(times, reverse=True)


# -- the four filters, alone and combined -------------------------------------------


def test_source_filter(live_client: TestClient, mint_token: Callable[..., str], seed: _Seed) -> None:
    token = mint_token(tenant_id=TENANT_A)
    manual = _ids(_list(live_client, token, "source=manual_csv_upload"))
    assert {seed.a_row_canonical, seed.a_row_source} <= manual
    assert seed.a_row_fk not in manual and seed.a_chunk_other not in manual  # sc_pos_v1

    sc_pos = _ids(_list(live_client, token, "source=sc_pos_v1"))
    assert {seed.a_row_fk, seed.a_chunk_other} <= sc_pos
    assert seed.a_row_canonical not in sc_pos


def test_error_type_filter_incl_other_bucket(
    live_client: TestClient, mint_token: Callable[..., str], seed: _Seed
) -> None:
    token = mint_token(tenant_id=TENANT_A)
    assert seed.a_row_canonical in _ids(_list(live_client, token, "error_type=canonical-shape"))
    assert seed.a_row_source in _ids(_list(live_client, token, "error_type=source-shape"))
    assert seed.a_row_fk in _ids(_list(live_client, token, "error_type=fk"))
    # The chunk's MAPPING_LOOKUP stage buckets to OTHER - present, never dropped.
    other = _ids(_list(live_client, token, "error_type=other"))
    assert seed.a_chunk_other in other
    assert seed.a_row_canonical not in other


def test_time_window_filter(live_client: TestClient, mint_token: Callable[..., str], seed: _Seed) -> None:
    token = mint_token(tenant_id=TENANT_A)
    last_24h = _ids(_list(live_client, token, "window=24h"))
    assert {seed.a_row_canonical, seed.a_chunk_other} <= last_24h  # recent
    assert seed.a_row_source not in last_24h and seed.a_row_fk not in last_24h  # 3d, 10d

    last_7d = _ids(_list(live_client, token, "window=7d"))
    assert seed.a_row_source in last_7d  # 3d
    assert seed.a_row_fk not in last_7d  # 10d

    assert seed.a_row_fk in _ids(_list(live_client, token, "window=30d"))


def test_status_open_returns_new_resolved_returns_empty(
    live_client: TestClient, mint_token: Callable[..., str], seed: _Seed
) -> None:
    token = mint_token(tenant_id=TENANT_A)
    open_ids = _ids(_list(live_client, token, "status=open"))
    assert {seed.a_row_canonical, seed.a_chunk_other} <= open_ids  # all seeded are NEW

    # resolved has no producing path (D82): none of the seeded NEW items appear.
    resolved_ids = _ids(_list(live_client, token, "status=resolved"))
    assert resolved_ids.isdisjoint(
        {seed.a_row_canonical, seed.a_row_source, seed.a_row_fk, seed.a_chunk_other}
    )


def test_combined_filters(live_client: TestClient, mint_token: Callable[..., str], seed: _Seed) -> None:
    token = mint_token(tenant_id=TENANT_A)
    # manual_csv_upload AND last 24h -> only the recent canonical row (source row is 3d).
    combined = _ids(_list(live_client, token, "source=manual_csv_upload&window=24h"))
    assert seed.a_row_canonical in combined
    assert seed.a_row_source not in combined
    assert seed.a_chunk_other not in combined  # sc_pos_v1, excluded by source


# -- open count: filter-independent -------------------------------------------------


def test_open_count_is_filter_independent(
    live_client: TestClient, mint_token: Callable[..., str], seed: _Seed
) -> None:
    token = mint_token(tenant_id=TENANT_A)
    unfiltered = _list(live_client, token)
    base_count = unfiltered["open_count"]
    unfiltered_items = unfiltered["items"]
    assert isinstance(base_count, int)
    assert isinstance(unfiltered_items, list)
    assert base_count >= seed.a_open_seeded  # at least the seeded NEW items

    # Apply a narrowing filter: fewer ITEMS, but the open_count badge is unchanged.
    narrowed = _list(live_client, token, "source=manual_csv_upload&window=24h")
    narrowed_items = narrowed["items"]
    assert isinstance(narrowed_items, list)
    assert len(narrowed_items) < len(unfiltered_items)  # the filter really narrowed
    assert narrowed["open_count"] == base_count  # the count did not


# -- detail (both kinds, unknown, cross-tenant) -------------------------------------


def test_detail_row(live_client: TestClient, mint_token: Callable[..., str], seed: _Seed) -> None:
    response = live_client.get(
        f"/api/v1/quarantine/{seed.a_row_canonical}", headers=_bearer(mint_token(tenant_id=TENANT_A))
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == seed.a_row_canonical
    assert body["kind"] == "row"
    assert body["failure_stage"] == "canonical-shape"
    assert body["error_reason"] == "VALIDATION_ROW_FAILED"
    assert body["mapping_version"] == TENANT_A_MAPPING_VERSION  # rows always carry it
    assert "price" in body["error_context"]
    assert body["chain_depth"] == 0
    assert body["original_payload"] is None  # DEFERRED, contract-stable null


def test_detail_chunk_has_null_version_and_payload(
    live_client: TestClient, mint_token: Callable[..., str], seed: _Seed
) -> None:
    response = live_client.get(
        f"/api/v1/quarantine/{seed.a_chunk_other}", headers=_bearer(mint_token(tenant_id=TENANT_A))
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "chunk"
    assert body["failure_stage"] == "other"  # MAPPING_LOOKUP -> OTHER bucket
    assert body["mapping_version"] is None  # pre-lookup chunk failure
    assert "mapping config invalid" in body["error_context"]
    assert body["original_payload"] is None


def test_detail_unknown_id_is_404(
    live_client: TestClient, mint_token: Callable[..., str], seed: _Seed
) -> None:
    response = live_client.get(
        f"/api/v1/quarantine/row:{new_uuid7()}", headers=_bearer(mint_token(tenant_id=TENANT_A))
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_detail_cross_tenant_id_is_404(
    live_client: TestClient, mint_token: Callable[..., str], seed: _Seed
) -> None:
    # A asks for B's held chunk by its real id: RLS hides it -> clean 404, no oracle.
    response = live_client.get(
        f"/api/v1/quarantine/{seed.b_chunk}", headers=_bearer(mint_token(tenant_id=TENANT_A))
    )
    assert response.status_code == 404


# -- Slice 17b: PLATFORM see-all + the conjunction gate, over the real HTTP read path ---


def test_list_platform_sees_all_tenants(
    live_client: TestClient, mint_token: Callable[..., str], seed: _Seed
) -> None:
    # The full require_read_scope -> read_session -> rls_platform_session seam over HTTP:
    # a PLATFORM+dis:ops token (no tenant_id) sees BOTH tenants' held items; a TENANT-A
    # token sees only A's. Proves see-all end to end, not merely the resolver.
    platform = mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:ops", "dis:read"))
    all_ids = _ids(_list(live_client, platform))
    assert {seed.a_row_canonical, seed.a_chunk_other} <= all_ids, "PLATFORM did not see tenant A's rows"
    assert seed.b_chunk in all_ids, "PLATFORM did not see tenant B's row -> see-all not wired over HTTP"

    a_only = _ids(_list(live_client, mint_token(tenant_id=TENANT_A)))
    assert seed.b_chunk not in a_only, "TENANT A saw tenant B's row -> isolation broken"


def test_list_platform_without_ops_is_denied(live_client: TestClient, mint_token: Callable[..., str]) -> None:
    # The conjunction gate over HTTP (decision 3): PLATFORM user_type alone is not enough --
    # without dis:ops, see-all is refused with a clean 403, no rows.
    token = mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:read",))
    response = live_client.get("/api/v1/quarantine", headers=_bearer(token))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ops_role_required"
