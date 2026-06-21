"""Pipeline paths against the LIVE stack: bronze target safety, the FAILED row,
PII raise-before-persist, and the resume-vs-no-op idempotency both ways (AC4/5/7).

Direct ``pipeline.process`` calls (real Postgres + real GCS emulator; an in-memory
publisher records the publishes). The Pub/Sub-delivered end-to-end proof lives in
``test_ingest_flow.py``. Errors, never skips: see ``conftest``.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url

from csv_ingest_worker.audit import WorkerAudit
from csv_ingest_worker.envelope import CsvReceivedEvent
from csv_ingest_worker.pipeline import IngestPipeline
from csv_ingest_worker.publisher import Publisher
from dis_audit import AuditBackend, select_writer
from dis_core.errors import PiiBackendNotConfiguredError, RlsContextError
from dis_core.ids import new_uuid7
from dis_core.timestamps import now_utc
from dis_storage import build_object_path
from dis_testing.fakes.pubsub import InMemoryPublisher
from dis_testing.fixtures import DEFAULT_SOURCE_ID, PRIMARY_STORE, PRIMARY_TENANT

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration

_GOOD_CSV = b"sku,store_section,qty_sold,unit_price\nA-1,front,5,9.99\nB-2,back,3,4.50\n"
# Same structure, semicolon-delimited — the European-export shape 16f exists for.
_SEMI_CSV = b"sku;store_section;qty_sold;unit_price\nA-1;front;5;9.99\nB-2;back;3;4.50\n"
_PII_CSV = b"sku,customer_email,qty_sold\nA-1,a@example.com,5\n"
_GARBAGE = b"\x00\x01\x02\xff\xfe not a csv \x00"


def _unique_session_id() -> str:
    """A fresh ^us_[a-z0-9]{12}$ session id so the 24h window can't couple runs."""
    return f"us_{new_uuid7().hex[:12]}"


def _make_event(storage: StorageClient, bucket: str, data: bytes, cleanup: list[UUID]) -> CsvReceivedEvent:
    """Play Phase 1 (dis-ui-server): mint trace, build the canonical path, upload,
    and assemble the csv.received envelope the WORKER will trust."""
    trace_id = new_uuid7()
    cleanup.append(trace_id)
    received = now_utc()
    key = build_object_path(
        tenant_id=PRIMARY_TENANT.uuid,
        source_id=DEFAULT_SOURCE_ID,
        trace_id=trace_id,
        event_ts=received,
        ext="csv",
    )
    storage.upload_bytes(key, data, content_type="text/csv")
    return CsvReceivedEvent(
        schema_version=1,
        trace_id=trace_id,
        tenant_id=PRIMARY_TENANT.uuid,
        store_id=PRIMARY_STORE.uuid,
        source_id=DEFAULT_SOURCE_ID,
        template_id=new_uuid7(),  # Slice 8 carry: required on the contract (D71)
        upload_session_id=_unique_session_id(),
        gcs_uri=f"gs://{bucket}/{key}",
        received_ts=received,
        tenant_display_code=PRIMARY_TENANT.display_code,
        store_code=PRIMARY_STORE.store_code,
    )


def _pipeline(
    engine: AsyncEngine, storage: StorageClient, bucket: str, publisher: Publisher
) -> IngestPipeline:
    return IngestPipeline(
        engine=engine,
        storage=storage,
        publisher=publisher,
        audit=WorkerAudit(select_writer(AuditBackend.POSTGRES, engine=engine)),
        bronze_bucket=bucket,
    )


def _bronze_row(dis_admin: Engine, trace_id: UUID) -> Any:
    with dis_admin.connect() as conn:
        return conn.execute(
            text(
                "SELECT id, tenant_id, store_id, source_id, dis_channel, trace_id, "
                "       payload_sha256, source_payload_id, row_count, processing_status, "
                "       published_at, received_at "
                "FROM bronze.data_ingress_events WHERE trace_id = :tid"
            ),
            {"tid": trace_id},
        ).one_or_none()


def _audit_stages(dis_admin: Engine, trace_id: UUID) -> list[tuple[str, str]]:
    with dis_admin.connect() as conn:
        rows = conn.execute(
            text("SELECT stage, outcome FROM audit.events WHERE trace_id = :tid ORDER BY event_timestamp"),
            {"tid": trace_id},
        ).all()
    return [(r.stage, r.outcome) for r in rows]


# ---------------------------------------------------------------------------
# AC5: target safety, asserted POSITIVELY, and the wrong-target refusal.
# ---------------------------------------------------------------------------


async def test_bronze_write_target_is_dis_db_on_5433_positively(
    engine: AsyncEngine, stack_env: dict[str, str]
) -> None:
    from dis_rls import rls_session

    url = make_url(stack_env["POSTGRES_URL"])
    assert url.port == 5433  # DIS port, never CM's 5432
    async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
        row = (await conn.execute(text("SELECT current_database() AS db, current_user AS role"))).one()
    assert row.db == "ithina_dis_db"
    assert row.role == "ithina_dis_user"


async def test_wrong_role_posture_raises_before_any_write(
    stack_env: dict[str, str], dis_admin: Engine
) -> None:
    # A superuser/BYPASSRLS connection (the admin role) must be REFUSED by the
    # session helper before a single statement runs — the wrong target/posture
    # exits before writing (Slice 7 pattern).
    from dis_rls import create_rls_engine, rls_session

    with dis_admin.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM bronze.data_ingress_events")).scalar()
    bypass_engine = create_rls_engine(stack_env["POSTGRES_ADMIN_URL"])
    try:
        with pytest.raises(RlsContextError):
            async with rls_session(bypass_engine, PRIMARY_TENANT.uuid):
                raise AssertionError("session must not open on a bypassing role")
    finally:
        await bypass_engine.dispose()
    with dis_admin.connect() as conn:
        after = conn.execute(text("SELECT count(*) FROM bronze.data_ingress_events")).scalar()
    assert before == after  # nothing written


# ---------------------------------------------------------------------------
# Happy path against the live schema (the e2e adds Pub/Sub delivery on top).
# ---------------------------------------------------------------------------


async def test_ingest_writes_bronze_then_publishes_then_marks(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = InMemoryPublisher()
    pipeline = _pipeline(engine, storage, bucket, publisher)
    event = _make_event(storage, bucket, _GOOD_CSV, cleanup_traces)

    outcome = await pipeline.process(event)

    assert outcome.disposition == "ingested"
    assert outcome.trace_id == event.trace_id  # read off the event, never minted
    row = _bronze_row(dis_admin, event.trace_id)
    assert row is not None
    assert row.id == outcome.bronze_id
    assert row.tenant_id == PRIMARY_TENANT.uuid
    assert row.store_id == PRIMARY_STORE.uuid  # single-store session, populated
    assert row.dis_channel == "csv_upload"
    assert row.payload_sha256 == hashlib.sha256(_GOOD_CSV).hexdigest()
    assert row.source_payload_id == event.upload_session_id
    assert row.row_count == 2
    assert row.processing_status == "PUBLISHED"  # marked after the publish
    assert row.published_at is not None

    [published] = publisher.messages_for("ingress.ready")
    envelope = json.loads(published)
    assert envelope["trace_id"] == str(event.trace_id)
    assert envelope["bronze_ref"] == str(row.id)
    assert envelope["tenant_display_code"] == PRIMARY_TENANT.display_code

    stages = _audit_stages(dis_admin, event.trace_id)
    assert ("RECEIVED", "SUCCESS") in stages
    assert ("PII_TOKENIZED", "SUCCESS") in stages
    assert ("BRONZE_WRITTEN", "SUCCESS") in stages
    assert ("INGRESS_PUBLISHED", "SUCCESS") in stages


async def test_rls_scopes_the_row_to_the_event_tenant(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    from dis_rls import rls_session
    from dis_testing.fixtures import TENANTS

    bucket = stack_env["GCS_BUCKET_BRONZE"]
    pipeline = _pipeline(engine, storage, bucket, InMemoryPublisher())
    event = _make_event(storage, bucket, _GOOD_CSV, cleanup_traces)
    await pipeline.process(event)

    other_tenant = TENANTS[1].uuid
    assert other_tenant != PRIMARY_TENANT.uuid
    async with rls_session(engine, other_tenant) as conn:
        visible = (
            await conn.execute(
                text("SELECT count(*) FROM bronze.data_ingress_events WHERE trace_id = :tid"),
                {"tid": event.trace_id},
            )
        ).scalar()
    assert visible == 0  # the other tenant cannot see the row (RLS isolation)


# ---------------------------------------------------------------------------
# AC3/OQ4: preflight failure -> FAILED bronze row, NO publish; redelivery no-ops.
# ---------------------------------------------------------------------------


async def test_preflight_failure_writes_failed_row_no_publish_then_noop(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = InMemoryPublisher()
    pipeline = _pipeline(engine, storage, bucket, publisher)
    event = _make_event(storage, bucket, _GARBAGE, cleanup_traces)

    outcome = await pipeline.process(event)
    assert outcome.disposition == "preflight_failed"
    row = _bronze_row(dis_admin, event.trace_id)
    assert row is not None
    assert row.processing_status == "FAILED"
    assert row.row_count is None
    assert publisher.messages_for("ingress.ready") == []  # conditional publish
    assert ("RECEIVED", "FAILURE") in _audit_stages(dis_admin, event.trace_id)

    # Redelivery of the same bad bytes: dedup absorbs it — no second row, no
    # re-preflight publish, prior trace returned.
    redelivery = await pipeline.process(event)
    assert redelivery.disposition == "duplicate_noop"
    assert redelivery.trace_id == event.trace_id
    assert publisher.messages_for("ingress.ready") == []
    with dis_admin.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM bronze.data_ingress_events WHERE trace_id = :tid"),
            {"tid": event.trace_id},
        ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# AC4: a recognized PII column raises BEFORE any persistence (live stack).
# ---------------------------------------------------------------------------


async def test_pii_header_raises_and_nothing_persists(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = InMemoryPublisher()
    pipeline = _pipeline(engine, storage, bucket, publisher)
    event = _make_event(storage, bucket, _PII_CSV, cleanup_traces)

    with pytest.raises(PiiBackendNotConfiguredError) as exc_info:
        await pipeline.process(event)
    assert "customer_email" in exc_info.value.columns
    assert _bronze_row(dis_admin, event.trace_id) is None  # raise preceded the write
    assert publisher.messages_for("ingress.ready") == []
    assert ("PII_TOKENIZED", "FAILURE") in _audit_stages(dis_admin, event.trace_id)


# ---------------------------------------------------------------------------
# AC7/D59: idempotency both ways against the live schema.
# ---------------------------------------------------------------------------


class _FailsOncePublisher:
    """Fails the FIRST publish (simulating a crash between write and publish)."""

    def __init__(self) -> None:
        self._inner = InMemoryPublisher()
        self._failed_once = False

    def publish(self, topic_name: str, data: bytes) -> str:
        if not self._failed_once:
            self._failed_once = True
            raise ConnectionError("injected publish outage")
        return self._inner.publish(topic_name, data)

    def messages_for(self, topic_name: str) -> list[bytes]:
        return self._inner.messages_for(topic_name)


async def test_published_prior_redelivery_is_full_noop(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = InMemoryPublisher()
    pipeline = _pipeline(engine, storage, bucket, publisher)
    event = _make_event(storage, bucket, _GOOD_CSV, cleanup_traces)

    first = await pipeline.process(event)
    second = await pipeline.process(event)  # redelivery

    assert first.disposition == "ingested"
    assert second.disposition == "duplicate_noop"
    assert second.trace_id == event.trace_id  # the prior trace_id returned
    assert len(publisher.messages_for("ingress.ready")) == 1  # no second publish
    with dis_admin.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM bronze.data_ingress_events WHERE trace_id = :tid"),
            {"tid": event.trace_id},
        ).scalar()
    assert count == 1  # no second bronze row
    # Slice 30c (the D42 revision): the dedup no-op's outcome IS the kind.
    assert ("RECEIVED", "DUPLICATE_NOOP") in _audit_stages(dis_admin, event.trace_id)


async def test_unpublished_prior_redelivery_resumes_and_marks(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = _FailsOncePublisher()
    pipeline = _pipeline(engine, storage, bucket, publisher)
    event = _make_event(storage, bucket, _GOOD_CSV, cleanup_traces)

    # First delivery: bronze lands, the publish dies (transient — the subscriber
    # would nack for redelivery).
    with pytest.raises(ConnectionError):
        await pipeline.process(event)
    row = _bronze_row(dis_admin, event.trace_id)
    assert row is not None
    assert row.processing_status == "RECEIVED"
    assert row.published_at is None  # bronze-first held; the publish was lost

    # Redelivery: resume-and-mark (D59) — complete the publish under the PRIOR
    # trace, stamp it, no second row.
    outcome = await pipeline.process(event)
    assert outcome.disposition == "duplicate_resumed"
    assert outcome.trace_id == event.trace_id
    [published] = publisher.messages_for("ingress.ready")
    envelope = json.loads(published)
    assert envelope["trace_id"] == str(event.trace_id)
    assert envelope["bronze_ref"] == str(row.id)
    marked = _bronze_row(dis_admin, event.trace_id)
    assert marked is not None
    assert marked.processing_status == "PUBLISHED"
    assert marked.published_at is not None
    with dis_admin.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM bronze.data_ingress_events WHERE trace_id = :tid"),
            {"tid": event.trace_id},
        ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# Slice 16f: the detected delimiter is carried on the published ingress.ready,
# on BOTH publish paths. Pins the PIPELINE WIRING (build_ingress_ready is unit-
# tested in isolation; these prove the pipeline passes preflight.delimiter, not a
# hardcoded comma) — fresh path AND the D59 resume re-derive.
# ---------------------------------------------------------------------------


async def test_fresh_ingest_publishes_the_detected_delimiter(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    cleanup_traces: list[UUID],
) -> None:
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = InMemoryPublisher()
    pipeline = _pipeline(engine, storage, bucket, publisher)
    event = _make_event(storage, bucket, _SEMI_CSV, cleanup_traces)

    outcome = await pipeline.process(event)

    assert outcome.disposition == "ingested"
    [published] = publisher.messages_for("ingress.ready")
    assert json.loads(published)["delimiter"] == ";"  # NOT a hardcoded comma


async def test_unpublished_prior_resume_republishes_the_detected_delimiter(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    cleanup_traces: list[UUID],
) -> None:
    # The D59 resume path runs no fresh preflight in process(); it re-derives the
    # delimiter from the same bytes. A ';' file must republish ';', never default
    # to comma on the rare crash-between-write-and-publish window.
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = _FailsOncePublisher()
    pipeline = _pipeline(engine, storage, bucket, publisher)
    event = _make_event(storage, bucket, _SEMI_CSV, cleanup_traces)

    with pytest.raises(ConnectionError):
        await pipeline.process(event)
    outcome = await pipeline.process(event)

    assert outcome.disposition == "duplicate_resumed"
    [published] = publisher.messages_for("ingress.ready")
    assert json.loads(published)["delimiter"] == ";"


# ---------------------------------------------------------------------------
# AC7: the idempotency check ERRORS (never skips) when its backing store is absent.
# ---------------------------------------------------------------------------


async def test_idempotency_check_errors_when_backing_store_absent(
    storage: StorageClient, stack_env: dict[str, str], cleanup_traces: list[UUID]
) -> None:
    # An engine pointed at a database that does not exist: the dedup lookup must
    # surface the connection failure loudly (the subscriber nacks for redelivery) —
    # NEVER return "no prior" and ingest a duplicate, never skip.
    from sqlalchemy.exc import InterfaceError, OperationalError

    from dis_rls import create_rls_engine

    bucket = stack_env["GCS_BUCKET_BRONZE"]
    dead_engine = create_rls_engine(
        "postgresql+psycopg://ithina_dis_user:wrong@localhost:5433/db_does_not_exist"
    )
    try:
        pipeline = _pipeline(dead_engine, storage, bucket, InMemoryPublisher())
        event = _make_event(storage, bucket, _GOOD_CSV, cleanup_traces)
        with pytest.raises((OperationalError, InterfaceError)):
            await pipeline.process(event)
    finally:
        await dead_engine.dispose()
