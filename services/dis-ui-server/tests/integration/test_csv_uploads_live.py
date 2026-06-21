"""``POST /api/v1/csv-uploads`` against the LIVE stack (Slice 8 acceptance).

Real everything: the RLS template resolve on the live ``config.source_mappings``
(template grain, 0005), the in-query store resolve on the live mirror, the GCS
emulator write at the D53 path, the real ``csv.received`` publish on the Pub/Sub
emulator (drained through a throwaway verification subscription and validated
against the frozen contract), and the live ``audit.events`` write.

The cross-tenant cases run on REAL seed data: tenant B's token naming tenant A's
template/store gets a clean 404 — never a resolve, never a 409 that would
confirm existence. A TRANSIENT INACTIVE store (the ``inactive_store`` fixture,
inserted under tenant A and reverted on teardown — the baseline mirror is
all-ACTIVE) proves the operator's ACTIVE-only 409 gate on real mirror rows.

Loud-error posture (the Slice 4/7 lesson): a missing stack env var ERRORS,
never skips.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine, text

from dis_storage import StorageClient, split_object_uri
from dis_ui_server.main import create_app

pytestmark = pytest.mark.integration

_CONTRACTS = Path(__file__).resolve().parents[3].parent / "contracts" / "pubsub"
_CSV_SCHEMA = json.loads((_CONTRACTS / "csv.received.schema.json").read_text())

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees (live mirror)
TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"  # zabka-group (live mirror)
_ACTIVE_STORE_A = "TX-101"  # live mirror: ACTIVE store of tenant A
_ACTIVE_STORE_B = "K-001"  # live mirror: ACTIVE store of tenant B
# The INACTIVE store is NOT in the baseline (all mirror rows are ACTIVE); it is a
# transient row inserted/reverted by the `inactive_store` fixture below.
_INACTIVE_STORE_ID = uuid.UUID("019e5e3c-0000-7000-8000-00000000409e")

_GOOD_CSV = b"sku,store_section,qty_sold,unit_price\nA-1,front,5,9.99\nB-2,back,3,4.50\n"

_UPLOAD_ENV = (
    "GCS_BUCKET_BRONZE",
    "PUBSUB_PROJECT_ID",
    "PUBSUB_EMULATOR_HOST",
    "STORAGE_EMULATOR_HOST",
)


class StackRequiredError(RuntimeError):
    """The local stack is required for these load-bearing tests but is absent."""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise StackRequiredError(
            f"{name} is not set — the Slice 8 upload integration tests refuse to skip "
            "silently. Bring up the stack (make run-local) and load .env."
        )
    return value


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def upload_env(stack_env: dict[str, str]) -> dict[str, str]:
    return {**stack_env, **{name: _require_env(name) for name in _UPLOAD_ENV}}


@pytest.fixture
def live_client(upload_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("POSTGRES_URL", upload_env["POSTGRES_URL"])
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def active_template_a(upload_env: dict[str, str]) -> dict[str, str]:
    """One live ACTIVE template of tenant A (admin read; the test's resolve truth)."""
    engine = create_engine(upload_env["POSTGRES_ADMIN_URL"])
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT template_id, source_id FROM config.source_mappings "
                    "WHERE tenant_id = :tid AND status = 'ACTIVE' "
                    "ORDER BY source_id LIMIT 1"
                ),
                {"tid": TENANT_A},
            ).one_or_none()
    finally:
        engine.dispose()
    assert row is not None, "no ACTIVE template seeded for tenant A — run make run-local"
    return {"template_id": str(row.template_id), "source_id": row.source_id}


@pytest.fixture
def inactive_store(upload_env: dict[str, str]) -> Iterator[str]:
    """A TRANSIENT INACTIVE store under tenant A (the baseline mirror is all-ACTIVE).

    Inserted directly into identity_mirror.stores via admin and DELETED on
    teardown so the mirror returns identical to its synced baseline (HARD REVERT
    RULE). Yields the store_code the 409-gate tests use.
    """
    engine = create_engine(upload_env["POSTGRES_ADMIN_URL"])
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO identity_mirror.stores "
                    "(store_id, tenant_id, name, store_code, status, country, timezone, "
                    " currency, tax_treatment, pc_created_at, pc_updated_at, mirror_synced_at) "
                    "VALUES (CAST(:sid AS uuid), CAST(:tid AS uuid), :name, :code, 'INACTIVE', "
                    " 'USA', 'America/Chicago', 'USD', 'EXCLUSIVE', now(), now(), now())"
                ),
                {
                    "sid": str(_INACTIVE_STORE_ID),
                    "tid": TENANT_A,
                    "name": "Transient Inactive (409 test)",
                    "code": "ZZ-INACTIVE",
                },
            )
        yield "ZZ-INACTIVE"
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM identity_mirror.stores WHERE store_id = CAST(:sid AS uuid)"),
                {"sid": str(_INACTIVE_STORE_ID)},
            )
        engine.dispose()


@pytest.fixture
def verify_subscription(upload_env: dict[str, str]) -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """A throwaway subscription on csv.received, created BEFORE the publish."""
    from google.cloud import pubsub_v1

    project = upload_env["PUBSUB_PROJECT_ID"]
    subscriber = pubsub_v1.SubscriberClient()
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project, "csv.received")
    sub_path = subscriber.subscription_path(project, f"slice8-verify-{uuid.uuid4().hex[:8]}")
    subscriber.create_subscription(request={"name": sub_path, "topic": topic_path})

    def drain() -> list[dict[str, Any]]:
        response = subscriber.pull(request={"subscription": sub_path, "max_messages": 10}, timeout=10)
        ack_ids = [m.ack_id for m in response.received_messages]
        if ack_ids:
            subscriber.acknowledge(request={"subscription": sub_path, "ack_ids": ack_ids})
        return [json.loads(m.message.data) for m in response.received_messages]

    try:
        yield drain
    finally:
        subscriber.delete_subscription(request={"subscription": sub_path})
        subscriber.close()


@pytest.fixture
def cleanup_audit(upload_env: dict[str, str]) -> Iterator[list[str]]:
    """Collects trace_ids; deletes their audit rows on teardown (live table hygiene)."""
    traces: list[str] = []
    yield traces
    if traces:
        engine = create_engine(upload_env["POSTGRES_ADMIN_URL"])
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM audit.events WHERE trace_id = ANY(CAST(:tids AS uuid[]))"),
                    {"tids": traces},
                )
        finally:
            engine.dispose()


def _post(
    client: TestClient,
    token: str,
    *,
    template_id: str,
    store_code: str,
    file_payload: bytes = _GOOD_CSV,
) -> Any:
    return client.post(
        "/api/v1/csv-uploads",
        headers=_bearer(token),
        data={"template_id": template_id, "store_code": store_code},
        files={"file": ("sales.csv", file_payload, "text/csv")},
    )


def test_valid_upload_lands_object_and_contract_valid_event(
    live_client: TestClient,
    mint_token: Callable[..., str],
    active_template_a: dict[str, str],
    upload_env: dict[str, str],
    verify_subscription: Callable[[], list[dict[str, Any]]],
    cleanup_audit: list[str],
) -> None:
    response = _post(
        live_client,
        mint_token(tenant_id=TENANT_A),
        template_id=active_template_a["template_id"],
        store_code=_ACTIVE_STORE_A,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    cleanup_audit.append(body["trace_id"])

    # The resolution truth: source derived from the LIVE template lineage.
    assert body["source_id"] == active_template_a["source_id"]
    assert body["tenant_id"] == TENANT_A

    # The object is REALLY at the D53 path on the (emulated) bucket.
    bucket, object_key = split_object_uri(body["gcs_uri"])
    assert bucket == upload_env["GCS_BUCKET_BRONZE"]
    assert StorageClient(bucket=bucket).download_bytes(object_key) == _GOOD_CSV

    # The event REALLY published, and the wire validates against the frozen
    # contract — required template_id included (D71 carry).
    [wire] = [m for m in verify_subscription() if m["trace_id"] == body["trace_id"]]
    Draft202012Validator(_CSV_SCHEMA, format_checker=FormatChecker()).validate(wire)
    assert wire["template_id"] == active_template_a["template_id"]
    assert wire["store_code"] == _ACTIVE_STORE_A
    assert wire["upload_session_id"] == body["upload_id"]


def test_cross_tenant_template_is_a_clean_404(
    live_client: TestClient,
    mint_token: Callable[..., str],
    active_template_a: dict[str, str],
) -> None:
    # Tenant B's token naming tenant A's REAL template: RLS makes it invisible —
    # indistinguishable from nonexistent (no oracle), and B's store stays unused.
    response = _post(
        live_client,
        mint_token(tenant_id=TENANT_B),
        template_id=active_template_a["template_id"],
        store_code=_ACTIVE_STORE_B,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_cross_tenant_store_code_is_a_clean_404_not_a_409(
    live_client: TestClient,
    mint_token: Callable[..., str],
    active_template_a: dict[str, str],
) -> None:
    # Tenant A's token naming tenant B's REAL, ACTIVE store: the in-query tenant
    # predicate makes it not match — a 404, never the 409 a lifecycle gate would
    # leak (which would confirm the store exists).
    response = _post(
        live_client,
        mint_token(tenant_id=TENANT_A),
        template_id=active_template_a["template_id"],
        store_code=_ACTIVE_STORE_B,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


@pytest.fixture
def tenant_b_active_template(upload_env: dict[str, str]) -> Iterator[str]:
    """A scoped ACTIVE template for tenant B (the live seed carries tenant A's
    only), inserted via admin and deleted on teardown. The seq trigger assigns
    version_seq; ACTIVE requires activated_at (live CHECK)."""
    import uuid as _uuid

    template_id = str(_uuid.uuid4())
    engine = create_engine(upload_env["POSTGRES_ADMIN_URL"])
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO config.source_mappings "
                    "(tenant_id, source_id, template_id, template_name, template_type, status, "
                    " mapping_rules, activated_at) "
                    "VALUES (CAST(:tid AS uuid), 'slice8_noorcle_src', "
                    " CAST(:tpl AS uuid), 'slice8-no-oracle', 'sales', 'ACTIVE', "
                    " CAST(:rules AS jsonb), NOW())"
                ),
                {"tid": TENANT_B, "tpl": template_id, "rules": '{"rename": {"sku": "sku_code"}}'},
            )
        yield template_id
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM config.source_mappings WHERE template_id = CAST(:tpl AS uuid)"),
                {"tpl": template_id},
            )
        engine.dispose()


def test_cross_tenant_inactive_store_is_404_never_a_state_leak(
    live_client: TestClient,
    mint_token: Callable[..., str],
    tenant_b_active_template: str,
    inactive_store: str,
) -> None:
    # The HARDEST no-oracle form: tenant A's transient INACTIVE store is REAL — if
    # the state gate ever ran before (or instead of) the tenant-scoped resolve, a
    # tenant-B caller would see the 409 and learn both that the store exists AND
    # its lifecycle state. It must be a 404 whose details carry no state at all.
    # Tenant B's own ACTIVE template lets the request pass the template step and
    # genuinely reach the store path.
    response = _post(
        live_client,
        mint_token(tenant_id=TENANT_B),
        template_id=tenant_b_active_template,
        store_code=inactive_store,  # tenant A's REAL INACTIVE store (transient)
    )
    assert response.status_code == 404  # never 409: the resolve failed first
    envelope = response.json()["error"]
    assert envelope["code"] == "resource_not_found"
    # No state leak in the details: nothing names the store's status or id.
    assert "actual" not in envelope["details"]
    assert "expected" not in envelope["details"]
    assert "store_id" not in envelope["details"]


def test_cross_tenant_template_leaks_neither_existence_nor_state(
    live_client: TestClient,
    mint_token: Callable[..., str],
    active_template_a: dict[str, str],
) -> None:
    # Tenant B naming tenant A's REAL ACTIVE template: 404 — never a 409 (which
    # would leak lifecycle state) and never a 500 (RLS invisibility must read as
    # plain not-found). Details carry only B's own inputs.
    response = _post(
        live_client,
        mint_token(tenant_id=TENANT_B),
        template_id=active_template_a["template_id"],
        store_code=_ACTIVE_STORE_B,
    )
    assert response.status_code == 404
    envelope = response.json()["error"]
    assert envelope["code"] == "resource_not_found"
    assert "actual" not in envelope["details"]  # no state vocabulary at all
    assert envelope["details"]["tenant_id"] == TENANT_B  # the CALLER's tenant only


def test_own_inactive_store_is_409_after_the_resolve(
    live_client: TestClient,
    mint_token: Callable[..., str],
    active_template_a: dict[str, str],
    inactive_store: str,
) -> None:
    # The operator's gate on REAL mirror data: the transient INACTIVE store is
    # tenant A's own — resolved fine (it IS the caller's), then refused 409
    # ACTIVE-only.
    response = _post(
        live_client,
        mint_token(tenant_id=TENANT_A),
        template_id=active_template_a["template_id"],
        store_code=inactive_store,
    )
    assert response.status_code == 409
    envelope = response.json()["error"]
    assert envelope["code"] == "store_state_conflict"
    assert envelope["details"]["expected"] == "ACTIVE"
    assert envelope["details"]["actual"] == "INACTIVE"
