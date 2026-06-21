"""End-to-end csv.received delivery proof (AC1/AC2): a test plays Phase 1 and
publishes csv.received on the REAL emulator topic; the worker's subscriber pulls,
processes, acks; ingress.ready arrives on a verify subscription; redelivery no-ops.

The delivery proof ERRORS, never skips, when the mechanism is absent: missing env
raises ``StackRequiredError`` (conftest), an absent topic/subscription raises
``NotFound``/``CsvIngestError`` — none of these is a pytest skip.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from csv_ingest_worker.audit import WorkerAudit
from csv_ingest_worker.config import CSV_RECEIVED_SUBSCRIPTION, CSV_RECEIVED_TOPIC
from csv_ingest_worker.pipeline import IngestPipeline
from csv_ingest_worker.subscriber import Subscriber
from dis_audit import AuditBackend, select_writer
from dis_core.errors import CsvIngestError
from dis_core.ids import new_uuid7
from dis_core.timestamps import now_utc
from dis_storage import build_object_path
from dis_testing.fakes.pubsub import EmulatorPublisher
from dis_testing.fixtures import DEFAULT_SOURCE_ID, PRIMARY_STORE, PRIMARY_TENANT
from dis_testing.pubsub import pubsub_test_project

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration

_GOOD_CSV = b"sku,store_section,qty_sold,unit_price\nA-1,front,5,9.99\nB-2,back,3,4.50\n"
_PROJECT = pubsub_test_project()  # D100: tests run on a project residents never subscribe to


def _unique_session_id() -> str:
    return f"us_{new_uuid7().hex[:12]}"


@pytest.fixture
def verify_subscription() -> Any:
    """An ad-hoc subscription on ingress.ready to observe the worker's publish."""
    from google.cloud import pubsub_v1

    client = pubsub_v1.SubscriberClient()
    name = f"test-verify-{new_uuid7().hex[:12]}"
    sub_path = client.subscription_path(_PROJECT, name)
    topic_path = client.topic_path(_PROJECT, "ingress.ready")
    client.create_subscription(request={"name": sub_path, "topic": topic_path})
    try:
        yield (client, sub_path)
    finally:
        client.delete_subscription(request={"subscription": sub_path})
        client.close()


def _drain(client: Any, sub_path: str) -> list[bytes]:
    """Pull-and-ack whatever is on the subscription right now."""
    bodies: list[bytes] = []
    while True:
        response = client.pull(
            request={"subscription": sub_path, "max_messages": 10},
            timeout=5,
            retry=None,
        )
        if not response.received_messages:
            return bodies
        bodies.extend(m.message.data for m in response.received_messages)
        client.acknowledge(
            request={
                "subscription": sub_path,
                "ack_ids": [m.ack_id for m in response.received_messages],
            }
        )


@pytest.fixture
def wired_subscriber(engine: AsyncEngine, storage: StorageClient, stack_env: dict[str, str]) -> Subscriber:
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    pipeline = IngestPipeline(
        engine=engine,
        storage=storage,
        publisher=EmulatorPublisher(project_id=_PROJECT),
        audit=WorkerAudit(select_writer(AuditBackend.POSTGRES, engine=engine)),
        bronze_bucket=bucket,
    )
    # Construction REQUIRES the provisioned subscription; absence raises loudly
    # (that property is asserted by test_absent_subscription_errors below).
    subscriber = Subscriber(project_id=_PROJECT, pipeline=pipeline)
    # Drain stale messages from previous runs so this test observes only its own.
    _drain(subscriber._client, subscriber._sub_path)
    return subscriber


async def test_end_to_end_csv_received_to_ingress_ready(
    wired_subscriber: Subscriber,
    verify_subscription: Any,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    verify_client, verify_path = verify_subscription

    # ---- Phase 1 (played by the test): object lands, csv.received published.
    trace_id = new_uuid7()
    cleanup_traces.append(trace_id)
    received = now_utc()
    key = build_object_path(
        tenant_id=PRIMARY_TENANT.uuid,
        source_id=DEFAULT_SOURCE_ID,
        trace_id=trace_id,
        event_ts=received,
        ext="csv",
    )
    storage.upload_bytes(key, _GOOD_CSV, content_type="text/csv")
    template_id = new_uuid7()  # Slice 8 carry: required on the contract (D71)
    event_payload = {
        "schema_version": 1,
        "trace_id": str(trace_id),
        "tenant_id": str(PRIMARY_TENANT.uuid),
        "store_id": str(PRIMARY_STORE.uuid),
        "tenant_display_code": PRIMARY_TENANT.display_code,
        "store_code": PRIMARY_STORE.store_code,
        "source_id": DEFAULT_SOURCE_ID,
        "template_id": str(template_id),
        "upload_session_id": _unique_session_id(),
        "gcs_uri": f"gs://{bucket}/{key}",
        "received_ts": received.isoformat(),
    }
    EmulatorPublisher(project_id=_PROJECT).publish(CSV_RECEIVED_TOPIC, json.dumps(event_payload).encode())

    # ---- Phase 2 (the worker under test): pull, process, ack.
    handled = await wired_subscriber.poll_once()
    assert handled >= 1

    # Bronze landed under the event's identity, marked published.
    with dis_admin.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, tenant_id, store_id, payload_sha256, processing_status, "
                "       published_at, template_id FROM bronze.data_ingress_events WHERE trace_id = :tid"
            ),
            {"tid": trace_id},
        ).one()
    assert row.tenant_id == PRIMARY_TENANT.uuid
    assert row.store_id == PRIMARY_STORE.uuid
    assert row.payload_sha256 == hashlib.sha256(_GOOD_CSV).hexdigest()
    assert row.processing_status == "PUBLISHED"
    assert row.template_id == template_id  # persisted for replay lineage (Slice 8 / D71)

    # ingress.ready arrived, carrying the EVENT's trace (read, never minted) and
    # the bronze pointer the streaming consumer needs.
    messages = _drain(verify_client, verify_path)
    envelopes = [json.loads(m) for m in messages]
    [envelope] = [e for e in envelopes if e["trace_id"] == str(trace_id)]
    assert envelope["bronze_ref"] == str(row.id)
    assert envelope["tenant_id"] == str(PRIMARY_TENANT.uuid)
    assert envelope["gcs_uri"] == event_payload["gcs_uri"]
    assert envelope["template_id"] == str(template_id)  # carried verbatim (D71)

    # The message was ACKed: another poll re-processes nothing for this trace.
    await wired_subscriber.poll_once()
    with dis_admin.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM bronze.data_ingress_events WHERE trace_id = :tid"),
            {"tid": trace_id},
        ).scalar()
    assert count == 1

    # ---- Redelivery (Pub/Sub at-least-once): same event published again -> the
    # idempotency path absorbs it: no second row, no second ingress.ready.
    EmulatorPublisher(project_id=_PROJECT).publish(CSV_RECEIVED_TOPIC, json.dumps(event_payload).encode())
    await wired_subscriber.poll_once()
    with dis_admin.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM bronze.data_ingress_events WHERE trace_id = :tid"),
            {"tid": trace_id},
        ).scalar()
    assert count == 1
    assert [
        e for e in map(json.loads, _drain(verify_client, verify_path)) if e["trace_id"] == str(trace_id)
    ] == []
    with dis_admin.connect() as conn:
        # Slice 30c (the D42 revision): the dedup no-op's outcome IS the kind,
        # and the prior trace is a COLUMN.
        noop = conn.execute(
            text(
                "SELECT count(*) FROM audit.events "
                "WHERE trace_id = :tid AND stage = 'RECEIVED' "
                "AND outcome = 'DUPLICATE_NOOP' AND prior_trace_id IS NOT NULL"
            ),
            {"tid": trace_id},
        ).scalar()
    assert noop == 1


async def test_absent_subscription_errors_never_skips(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The worker REQUIRES its provisioned subscription at startup: pointing it at a
    # name that does not exist must raise loudly (no auto-create, no skip).
    import csv_ingest_worker.subscriber as subscriber_module

    monkeypatch.setattr(subscriber_module, "CSV_RECEIVED_SUBSCRIPTION", "does-not-exist.csv.received")
    pipeline = IngestPipeline(
        engine=engine,
        storage=storage,
        publisher=EmulatorPublisher(project_id=_PROJECT),
        audit=WorkerAudit(select_writer(AuditBackend.POSTGRES, engine=engine)),
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    with pytest.raises(CsvIngestError, match="make topics-create"):
        Subscriber(project_id=_PROJECT, pipeline=pipeline)


def test_provisioned_subscription_matches_the_frozen_constant() -> None:
    # The provisioning source of truth (dis_core.pubsub_names, which both
    # `make topics-create` and the dis-testing harness provision from) and the
    # worker's constants must name the SAME subscription/topic, or startup would
    # always fail.
    from dis_core.pubsub_names import PUBSUB_SUBSCRIPTIONS, PUBSUB_TOPICS

    assert CSV_RECEIVED_SUBSCRIPTION in PUBSUB_SUBSCRIPTIONS
    assert CSV_RECEIVED_TOPIC in PUBSUB_TOPICS
    assert PUBSUB_SUBSCRIPTIONS[CSV_RECEIVED_SUBSCRIPTION] == CSV_RECEIVED_TOPIC
