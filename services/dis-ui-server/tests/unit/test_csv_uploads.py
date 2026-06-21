"""``POST /api/v1/csv-uploads`` orchestration: every error path + the happy path.

The repos are monkeypatched (the live-RLS resolution paths are the integration
suite's); GCS/Pub/Sub/audit are recording fakes swapped onto ``app.state`` after
startup. What this module proves is the slice's acceptance list: token-only
identity (a smuggled body tenant_id is ignored), the gate ORDER (tier-0 before
any resolve, store 404 before the 409 lifecycle gate, no GCS write or publish on
any 4xx), the §2.3 envelope on every error, the published wire validating
against the frozen contract, and the deterministic ``upload_id``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from dis_audit import AuditEvent, Outcome, Stage
from dis_core.errors import ResourceNotFoundError
from dis_ui_server.handlers.csv_uploads import derive_upload_session_id
from dis_ui_server.main import create_app

_CONTRACTS = Path(__file__).resolve().parents[3].parent / "contracts" / "pubsub"
_CSV_SCHEMA = json.loads((_CONTRACTS / "csv.received.schema.json").read_text())

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"
TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"
_STORE_ID = UUID("019e5e3c-b62e-75e6-ad62-529127ae944a")
_TEMPLATE_ID = "019e98c9-df80-7649-98cd-83fb6293777a"
_GOOD_CSV = b"sku,qty\nA-1,5\nB-2,3\n"

_HANDLER_MODULE = "dis_ui_server.handlers.csv_uploads"


class _FakeStorage:
    def __init__(self, explode: bool = False) -> None:
        self.explode = explode
        self.uploads: list[tuple[str, bytes, str | None]] = []

    def upload_bytes(self, object_path: str, data: bytes, *, content_type: str | None = None) -> None:
        if self.explode:
            raise ConnectionError("injected GCS outage")
        self.uploads.append((object_path, data, content_type))


class _FakePublisher:
    def __init__(self, explode: bool = False) -> None:
        self.explode = explode
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish(self, topic_name: str, data: bytes) -> str:
        if self.explode:
            raise ConnectionError("injected Pub/Sub outage")
        self.published.append((topic_name, json.loads(data)))
        return "msg-1"


class _RecordingAuditWriter:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.explode = False

    async def write(self, event: AuditEvent) -> bool:
        if self.explode:
            raise RuntimeError("injected audit backend failure")
        self.events.append(event)
        return True


@pytest.fixture
def harness(
    unit_env: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter]]:
    """The app with recording fakes and happy-path repo stubs (overridable)."""

    async def fake_resolve_template(engine: Any, tenant_id: UUID, template_id: UUID) -> Any:
        return SimpleNamespace(
            source_id="sc_pos_v1", template_id=template_id, status="ACTIVE", mapping_version_id=2
        )

    async def fake_resolve_store(engine: Any, tenant_id: UUID, store_code: str) -> Any:
        return SimpleNamespace(store_id=_STORE_ID, store_code=store_code, status="ACTIVE")

    async def fake_display_code(engine: Any, tenant_id: UUID) -> str | None:
        return "buc-ees"

    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_active_template", fake_resolve_template)
    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_store_by_code", fake_resolve_store)
    monkeypatch.setattr(f"{_HANDLER_MODULE}.get_tenant_display_code", fake_display_code)

    storage = _FakeStorage()
    publisher = _FakePublisher()
    writer = _RecordingAuditWriter()
    with TestClient(create_app()) as client:
        from dis_ui_server.audit import UiAudit

        client.app.state.storage = storage  # type: ignore[attr-defined]
        client.app.state.publisher = publisher  # type: ignore[attr-defined]
        client.app.state.audit = UiAudit(writer)  # type: ignore[attr-defined]
        yield client, storage, publisher, writer


def _post(
    client: TestClient,
    token: str,
    *,
    file_payload: bytes = _GOOD_CSV,
    template_id: str = _TEMPLATE_ID,
    store_code: str = "TX-101",
    extra_fields: dict[str, str] | None = None,
    omit: frozenset[str] = frozenset(),
) -> Any:
    data = {"template_id": template_id, "store_code": store_code, **(extra_fields or {})}
    for name in omit:
        data.pop(name, None)
    if "file" in omit:
        # Keep the encoding multipart (httpx falls back to urlencoded with no
        # files); the unknown part is drained by the parser, file stays missing.
        files = {"unrelated": ("x.bin", b"ignored", "application/octet-stream")}
    else:
        files = {"file": ("sales.csv", file_payload, "text/csv")}
    return client.post(
        "/api/v1/csv-uploads",
        headers={"Authorization": f"Bearer {token}"},
        data=data,
        files=files,
    )


# ---------------------------------------------------------------------------
# Happy path: D53 path, frozen-contract wire, audit, the 201 body.
# ---------------------------------------------------------------------------


def test_valid_upload_writes_d53_path_publishes_contract_valid_event_and_audits(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    client, storage, publisher, writer = harness
    response = _post(client, mint_token())
    assert response.status_code == 201, response.text
    body = response.json()

    # The 201 body: resolved identity + pointers; source derived from the template.
    assert body["tenant_id"] == TENANT_A
    assert body["store_id"] == str(_STORE_ID)
    assert body["source_id"] == "sc_pos_v1"
    assert body["template_id"] == _TEMPLATE_ID
    assert body["row_count"] == 2
    assert body["status"] == "received"

    # Exactly one object at the canonical D53 path (UUID tenant segment).
    [(object_key, data, content_type)] = storage.uploads
    assert object_key.startswith(f"tenant/{TENANT_A}/source/sc_pos_v1/yyyy=")
    assert object_key.endswith(f"{body['trace_id']}.csv")
    assert data == _GOOD_CSV
    assert content_type == "text/csv"
    assert body["gcs_uri"] == f"gs://ithina-bronze-raw/{object_key}"

    # Exactly one publish; the wire validates against the FROZEN contract.
    [(topic, wire)] = publisher.published
    assert topic == "csv.received"
    Draft202012Validator(_CSV_SCHEMA, format_checker=FormatChecker()).validate(wire)
    assert wire["tenant_id"] == TENANT_A
    assert wire["store_id"] == str(_STORE_ID)
    assert wire["template_id"] == _TEMPLATE_ID
    assert wire["trace_id"] == body["trace_id"]
    assert wire["upload_session_id"] == body["upload_id"]
    assert wire["tenant_display_code"] == "buc-ees"
    assert wire["store_code"] == "TX-101"

    # One SUCCESS audit, receiver-context populated.
    [event] = [e for e in writer.events if e.outcome is Outcome.SUCCESS]
    assert event.stage is Stage.RECEIVED
    assert str(event.tenant_id) == TENANT_A
    assert str(event.trace_id) == body["trace_id"]
    assert event.auth_principal == "user:user-1"
    assert event.event_data is not None
    assert event.event_data["phase"] == "csv_upload_phase1"
    # Slice 30b: whole-request elapsed on the single audit row.
    assert event.duration_ms is not None and event.duration_ms >= 0


def test_smuggled_body_tenant_id_is_ignored(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    # AC: tenant comes ONLY from the token; a body tenant_id changes nothing.
    client, storage, publisher, _ = harness
    response = _post(client, mint_token(), extra_fields={"tenant_id": TENANT_B})
    assert response.status_code == 201
    [(topic, wire)] = publisher.published
    assert wire["tenant_id"] == TENANT_A  # the TOKEN tenant, not the smuggled one
    [(object_key, _, _)] = storage.uploads
    assert object_key.startswith(f"tenant/{TENANT_A}/")  # the path too


def test_upload_id_is_deterministic_and_pattern_conformant(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    # The resolved idempotency mechanic: same bytes + identity → the same id
    # (so a client retry collapses in the worker's D58 dedup); any component
    # change → a different id.
    client, _, publisher, _ = harness
    token = mint_token()
    first = _post(client, token).json()
    second = _post(client, token).json()
    assert first["upload_id"] == second["upload_id"]  # retry-stable
    assert first["trace_id"] != second["trace_id"]  # every attempt is its own trace
    changed = _post(client, token, file_payload=b"sku,qty\nZ-9,1\n").json()
    assert changed["upload_id"] != first["upload_id"]  # content-sensitive

    import re

    assert re.fullmatch(r"us_[a-z0-9]{12}", first["upload_id"])
    for _, wire in publisher.published:
        assert wire["upload_session_id"].startswith("us_")


def test_derive_upload_session_id_components_all_matter() -> None:
    tenant, store, template = UUID(TENANT_A), _STORE_ID, UUID(_TEMPLATE_ID)
    base = derive_upload_session_id(tenant, store, template, "a" * 64)
    assert base == derive_upload_session_id(tenant, store, template, "a" * 64)
    assert base != derive_upload_session_id(UUID(TENANT_B), store, template, "a" * 64)
    assert base != derive_upload_session_id(tenant, UUID(TENANT_B), template, "a" * 64)
    assert base != derive_upload_session_id(tenant, store, UUID(TENANT_B), "a" * 64)
    assert base != derive_upload_session_id(tenant, store, template, "b" * 64)


# ---------------------------------------------------------------------------
# Auth: 401/403 before anything else.
# ---------------------------------------------------------------------------


def test_missing_bearer_is_401(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
) -> None:
    client, storage, publisher, _ = harness
    response = client.post("/api/v1/csv-uploads", files={"file": ("x.csv", _GOOD_CSV)})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_token"
    assert not storage.uploads and not publisher.published


def test_tenantless_token_is_403(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    client, storage, publisher, _ = harness
    response = _post(client, mint_token(tenant_id=None, user_type="PLATFORM"))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_scope"
    assert not storage.uploads and not publisher.published


# ---------------------------------------------------------------------------
# Request-shape and tier-0 rejections: clean 4xx, NOTHING persisted or published.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_part", ["file", "template_id", "store_code"])
def test_missing_part_is_400_with_no_side_effects(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
    missing_part: str,
) -> None:
    client, storage, publisher, writer = harness
    response = _post(client, mint_token(), omit=frozenset({missing_part}))
    assert response.status_code == 400
    envelope = response.json()["error"]
    assert envelope["code"] == "upload_request"
    assert envelope["details"]["part"] == missing_part
    assert not storage.uploads and not publisher.published
    # Slice 30b: the 4xx family IS audited (emit-then-re-raise; the envelope above
    # proves the HTTP semantics are unchanged).
    [event] = writer.events
    assert event.outcome is Outcome.FAILURE
    assert event.failure_code == "UPLOAD_REQUEST_MALFORMED"
    assert event.event_data is not None and event.event_data["step"] == "upload"
    assert event.auth_principal is not None


def test_malformed_template_id_is_400(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    client, storage, publisher, _ = harness
    response = _post(client, mint_token(), template_id="not-a-uuid")
    assert response.status_code == 400
    assert response.json()["error"]["details"]["part"] == "template_id"
    assert "not-a-uuid" not in response.json()["error"]["message"]  # never echoed
    assert not storage.uploads and not publisher.published


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"", "empty_file"),
        (b"sku,qty\n\xff\xfe\x80\n", "not_utf8"),
        (b"sku,qty\n", "below_min_rows"),
    ],
)
def test_tier0_failure_is_422_with_no_gcs_write_and_no_publish(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
    payload: bytes,
    reason: str,
) -> None:
    client, storage, publisher, writer = harness
    response = _post(client, mint_token(), file_payload=payload)
    assert response.status_code == 422
    envelope = response.json()["error"]
    assert envelope["code"] == "upload_structure"
    assert envelope["details"]["reason"] == reason
    assert envelope["trace_id"] is not None  # minted at entry; on every envelope
    assert not storage.uploads and not publisher.published
    # Slice 30b: the tier-0 rejection IS audited, reason-grained stable code.
    expected_code = {
        "empty_file": "UPLOAD_EMPTY_FILE",
        "not_utf8": "UPLOAD_NOT_UTF8",
        "not_csv": "UPLOAD_NOT_CSV",
        "below_min_rows": "UPLOAD_BELOW_MIN_ROWS",
    }[reason]
    [event] = writer.events
    assert event.outcome is Outcome.FAILURE
    assert event.failure_code == expected_code
    assert event.event_data is not None and event.event_data["reason"] == reason


def test_tier0_runs_before_any_resolution(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty file must 422 BEFORE the template/store repos are consulted.
    client, _, _, _ = harness

    async def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resolution must not run before the tier-0 gate")

    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_active_template", explode)
    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_store_by_code", explode)
    response = _post(client, mint_token(), file_payload=b"")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Resolution failures: 404 (no oracle) and the two 409 lifecycle gates.
# ---------------------------------------------------------------------------


def test_unknown_or_cross_tenant_template_is_404(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, storage, publisher, writer = harness

    async def not_found(engine: Any, tenant_id: UUID, template_id: UUID) -> Any:
        raise ResourceNotFoundError(
            f"mapping template {template_id} not found",
            resource="mapping_template",
            identifier=str(template_id),
            tenant_id=str(tenant_id),
        )

    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_active_template", not_found)
    response = _post(client, mint_token())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"
    assert not storage.uploads and not publisher.published
    # Slice 30b: audited with the step-disambiguated code (template, not store).
    [event] = writer.events
    assert event.outcome is Outcome.FAILURE and event.failure_code == "TEMPLATE_NOT_FOUND"


def test_non_active_template_is_409(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dis_core.errors import MappingStateConflictError

    client, storage, publisher, writer = harness

    async def draft_only(engine: Any, tenant_id: UUID, template_id: UUID) -> Any:
        raise MappingStateConflictError(
            "no ACTIVE version",
            template_id=str(template_id),
            tenant_id=str(tenant_id),
            expected="ACTIVE",
            actual="DRAFT",
        )

    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_active_template", draft_only)
    response = _post(client, mint_token())
    assert response.status_code == 409
    envelope = response.json()["error"]
    assert envelope["code"] == "mapping_state_conflict"
    assert envelope["details"]["actual"] == "DRAFT"
    assert not storage.uploads and not publisher.published
    [event] = writer.events  # Slice 30b
    assert event.outcome is Outcome.FAILURE and event.failure_code == "TEMPLATE_NOT_ACTIVE"


def test_unresolvable_store_code_is_404(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, storage, publisher, writer = harness

    async def not_found(engine: Any, tenant_id: UUID, store_code: str) -> Any:
        raise ResourceNotFoundError(
            f"store_code {store_code!r} does not resolve",
            resource="store",
            identifier=store_code,
            tenant_id=str(tenant_id),
        )

    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_store_by_code", not_found)
    response = _post(client, mint_token(), store_code="K-001")  # another tenant's code
    assert response.status_code == 404  # a 404, NEVER a 409 oracle
    assert response.json()["error"]["code"] == "resource_not_found"
    assert not storage.uploads and not publisher.published
    [event] = writer.events  # Slice 30b: store step, not template
    assert event.outcome is Outcome.FAILURE and event.failure_code == "STORE_NOT_FOUND"


@pytest.mark.parametrize("status", ["OPENING", "INACTIVE", "CLOSED"])
def test_resolved_but_non_active_store_is_409_after_the_404_gate(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    # The operator-decided gate: ACTIVE only, 409, applied only AFTER a
    # successful tenant-scoped resolve (this store IS the caller's).
    client, storage, publisher, writer = harness

    async def non_active_store(engine: Any, tenant_id: UUID, store_code: str) -> Any:
        return SimpleNamespace(store_id=_STORE_ID, store_code=store_code, status=status)

    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_store_by_code", non_active_store)
    response = _post(client, mint_token(), store_code="TX-102")
    assert response.status_code == 409
    envelope = response.json()["error"]
    assert envelope["code"] == "store_state_conflict"
    assert envelope["details"]["expected"] == "ACTIVE"
    assert envelope["details"]["actual"] == status
    assert not storage.uploads and not publisher.published
    [event] = writer.events  # Slice 30b
    assert event.outcome is Outcome.FAILURE and event.failure_code == "STORE_NOT_ACTIVE"


# ---------------------------------------------------------------------------
# Dependency failures: 503, ordering, the accepted-orphan posture, FAILURE audit.
# ---------------------------------------------------------------------------


def test_gcs_write_failure_is_503_and_nothing_is_published(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    client, storage, publisher, writer = harness
    storage.explode = True
    response = _post(client, mint_token())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage"
    assert not publisher.published  # write-then-publish: no write, no event
    [event] = [e for e in writer.events if e.outcome is Outcome.FAILURE]
    assert event.failure_code == "GCS_WRITE_FAILED"  # Slice 30b stable vocabulary


def test_publish_failure_is_503_with_the_object_as_an_accepted_orphan(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    client, storage, publisher, writer = harness
    publisher.explode = True
    response = _post(client, mint_token())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "event_publish"
    # The orphan posture: the object IS written and deliberately NOT deleted —
    # a client retry converges via the deterministic upload_id instead.
    assert len(storage.uploads) == 1
    [event] = [e for e in writer.events if e.outcome is Outcome.FAILURE]
    assert event.failure_code == "PUBLISH_FAILED"  # Slice 30b stable vocabulary


def test_exploding_audit_backend_never_blocks_the_upload(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    # Hard rule 11 at this service's audit seam (the repo's second emitter): a
    # raising audit writer must not turn a successful upload into an error —
    # the object is written, the event published, the 201 served.
    client, storage, publisher, writer = harness
    writer.explode = True
    response = _post(client, mint_token())
    assert response.status_code == 201
    assert len(storage.uploads) == 1
    assert len(publisher.published) == 1


def test_exploding_audit_backend_never_changes_a_4xx_response(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Slice 30b HTTP-safety property at the 4xx wrap specifically: the wrap
    is emit-then-re-raise, so a THROWING audit backend on a rejection path must
    leave the response the exact original clean 4xx (status + §2.3 envelope,
    trace_id excluded — it is per-request by design), never a 500, never a
    wrapped/replaced error. Driven for a 422 (tier-0) and a 404 (template)."""
    client, storage, publisher, writer = harness

    async def template_not_found(engine: Any, tenant_id: UUID, template_id: UUID) -> Any:
        raise ResourceNotFoundError(
            f"mapping template {template_id} not found",
            resource="mapping_template",
            identifier=str(template_id),
            tenant_id=str(tenant_id),
        )

    def envelope_sans_trace(response: Any) -> dict[str, Any]:
        envelope = response.json()["error"]
        envelope.pop("trace_id")  # minted per request; everything else must match
        if isinstance(envelope.get("details"), dict):
            envelope["details"].pop("trace_id", None)  # error-class context, also per-request
        return dict(envelope)

    # 422 tier-0 (empty file): baseline, then byte-identical under explosion.
    baseline_422 = _post(client, mint_token(), file_payload=b"")
    writer.explode = True
    exploded_422 = _post(client, mint_token(), file_payload=b"")
    assert exploded_422.status_code == baseline_422.status_code == 422
    assert envelope_sans_trace(exploded_422) == envelope_sans_trace(baseline_422)

    # 404 template: same proof on a resolution-step rejection.
    writer.explode = False
    monkeypatch.setattr(f"{_HANDLER_MODULE}.resolve_active_template", template_not_found)
    baseline_404 = _post(client, mint_token())
    writer.explode = True
    exploded_404 = _post(client, mint_token())
    assert exploded_404.status_code == baseline_404.status_code == 404
    assert envelope_sans_trace(exploded_404) == envelope_sans_trace(baseline_404)

    # Nothing leaked into side effects on any of the four rejected requests.
    assert not storage.uploads and not publisher.published


def test_every_error_envelope_carries_this_requests_trace(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    # The bind-without-reset design (the §2.3 handlers render AFTER the handler
    # unwinds): every error path's envelope must carry a real, per-request trace
    # — 413 mid-stream, 400, 422 tier-0, 404 template, 409 store state, 503 GCS,
    # 503 publish. A None here means the bind was lost before rendering.
    client, storage, publisher, _ = harness
    token = mint_token()
    traces: list[str] = []

    def _trace_of(response: Any, expected_status: int) -> None:
        assert response.status_code == expected_status, response.text
        trace = response.json()["error"]["trace_id"]
        assert trace is not None
        UUID(trace)  # parses — a real minted trace, not a placeholder
        traces.append(trace)

    _trace_of(_post(client, token, file_payload=b"x" * (11 * 1024 * 1024)), 413)
    _trace_of(_post(client, token, template_id="not-a-uuid"), 400)
    _trace_of(_post(client, token, file_payload=b""), 422)

    async def template_404(*args: Any, **kwargs: Any) -> Any:
        raise ResourceNotFoundError("gone", resource="mapping_template", identifier="x")

    async def closed_store(engine: Any, tenant_id: UUID, store_code: str) -> Any:
        return SimpleNamespace(store_id=_STORE_ID, store_code=store_code, status="CLOSED")

    # Scoped contexts: a bare monkeypatch.undo() would also strip the HARNESS's
    # repo stubs (same function-scoped instance) and reach for the real DB.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(f"{_HANDLER_MODULE}.resolve_active_template", template_404)
        _trace_of(_post(client, token), 404)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(f"{_HANDLER_MODULE}.resolve_store_by_code", closed_store)
        _trace_of(_post(client, token), 409)

    storage.explode = True
    _trace_of(_post(client, token), 503)
    storage.explode = False
    publisher.explode = True
    _trace_of(_post(client, token), 503)
    publisher.explode = False

    # And no two requests shared a trace.
    assert len(set(traces)) == len(traces)


def test_trace_cannot_bleed_across_sequential_requests(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    # The adversarial case for bind-WITHOUT-reset: the binding must die with the
    # request's task. Two sequential requests on the same client/event loop —
    # request 2's envelope (and a request-2 SUCCESS publish) must carry its OWN
    # trace, never request 1's.
    client, _, publisher, _ = harness
    token = mint_token()
    first = _post(client, token, file_payload=b"")  # 422, binds trace T1
    t1 = first.json()["error"]["trace_id"]
    assert t1 is not None

    second = _post(client, token, file_payload=b"")  # 422, must bind its own T2
    t2 = second.json()["error"]["trace_id"]
    assert t2 is not None
    assert t2 != t1  # no bleed error→error

    third = _post(client, token)  # SUCCESS after two bound-and-unreset errors
    assert third.status_code == 201
    t3 = third.json()["trace_id"]
    assert t3 not in (t1, t2)
    [(_, wire)] = publisher.published
    assert wire["trace_id"] == t3  # the published event carries ITS request's trace


def test_retry_after_publish_failure_converges_on_the_same_upload_id(
    harness: tuple[TestClient, _FakeStorage, _FakePublisher, _RecordingAuditWriter],
    mint_token: Any,
) -> None:
    # The client-retry idempotency story, end to end at this hop: attempt 1
    # orphans an object (publish down); the retry of the SAME bytes succeeds
    # and carries the SAME upload_session_id, so the worker's D58 dedup sees
    # one logical upload.
    client, storage, publisher, _ = harness
    token = mint_token()
    publisher.explode = True
    failed = _post(client, token)
    assert failed.status_code == 503
    publisher.explode = False
    retried = _post(client, token)
    assert retried.status_code == 201
    [(_, wire)] = publisher.published
    assert wire["upload_session_id"] == retried.json()["upload_id"]
    assert len(storage.uploads) == 2  # the orphan + the good attempt (distinct traces)
    first_key, second_key = storage.uploads[0][0], storage.uploads[1][0]
    assert first_key != second_key  # trace-keyed paths: a retry never overwrites
