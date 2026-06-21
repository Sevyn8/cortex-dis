"""The Slice 11a failure disposition through the REAL subscriber.

Two complementary proofs over the live Pub/Sub emulator:

- THE STORM-FIX PROOF: a deterministic gate failure (the pre-mapping
  missing-column chunk — row-less, so chunk-grain) is HELD in
  ``quarantine.quarantined_chunks`` and the message is ACKED: pulled once and
  NEVER redelivered. The pre-11a redeliver-forever loop is broken at its source.
- THE SCOPE PROOF: a failure OFF the allowlist — the store-miss
  ``CONTRACT_VIOLATION`` (``field='store_id'``), carved out because mirror-sync
  lag heals it (the governing principle: retry is its designed recovery) — still
  NACKS and the emulator REDELIVERS it (deadline 0), exactly today's behavior.

Plus the 11a boundary pin: the quarantine SINK exists (direct write), but no
``quarantine`` TOPIC publish and no drainer exist — that is Slice 11b.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import text

from dis_testing.fakes.pubsub import EmulatorPublisher
from dis_testing.pubsub import pubsub_test_project
from streaming_consumer.clients.pubsub import Subscriber, process_message
from streaming_consumer.config import INGRESS_READY_TOPIC
from streaming_consumer.orchestrate import ConsumerPipeline

from .conftest import SALE_SOURCE_ID, Cleanup, drain_subscription, seed_chunk

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration

_PROJECT = pubsub_test_project()  # D100: tests run on a project residents never subscribe to

# Missing the 'qty' column: fails the pre-mapping gate deterministically with a
# ROW-LESS failure shape (column-absent), so 11a holds it at CHUNK grain.
_BAD_CSV = b"sold_at,sku,retail,price,txn,line\n2026-01-01 10:00:00,X,9.99,8.50,T,1\n"


def _held_chunks(dis_admin: Engine, trace_id: object) -> int:
    with dis_admin.begin() as conn:
        return int(
            conn.execute(
                text("SELECT COUNT(*) FROM quarantine.quarantined_chunks WHERE trace_id = CAST(:t AS uuid)"),
                {"t": str(trace_id)},
            ).scalar_one()
        )


def _contract_failure_audits(dis_admin: Engine, trace_id: object) -> int:
    with dis_admin.begin() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM audit.events WHERE trace_id = CAST(:t AS uuid) "
                    "AND outcome = 'FAILURE' AND failure_code = 'CONTRACT_VIOLATION'"
                ),
                {"t": str(trace_id)},
            ).scalar_one()
        )


async def test_deterministic_gate_failure_is_quarantined_and_acked(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
) -> None:
    """THE STORM-FIX PROOF at the subscriber: held, acked, never redelivered."""
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=_BAD_CSV,
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    payload = chunk.event.model_dump_json(exclude_none=True).encode()

    # The routing decision itself: a quarantined disposition ACKS. (This direct
    # call holds the chunk once — the per-trace held-count baseline is 1.)
    decision = await process_message(pipeline, payload)
    assert decision == "ack"
    assert _held_chunks(dis_admin, chunk.trace_id) == 1

    # Through the real subscriber: publish, pull (processed + acked), then
    # observe the emulator does NOT redeliver it — the loop is broken. (Pre-11a
    # this exact chunk redelivered forever; that proof is now the scope test
    # below, on a non-allowlisted failure.) The evidence is PER-TRACE (the held
    # count for THIS chunk: every processing holds it once more — D44-style
    # duplicate tolerance, no dedup key), because the subscription is shared and
    # other suites (the worker publishes ingress.ready) leave stray messages
    # that poll counts would miscount.
    drain_subscription(_PROJECT)  # start clean of other suites' strays
    try:
        publisher = EmulatorPublisher(project_id=_PROJECT)
        publisher.publish(INGRESS_READY_TOPIC, payload)
        subscriber = Subscriber(project_id=_PROJECT, pipeline=pipeline, max_messages=10)
        held = 1
        for _ in range(15):
            await subscriber.poll_once()
            held = _held_chunks(dis_admin, chunk.trace_id)
            if held >= 2:  # the subscriber delivery processed (and held) our chunk
                break
        assert held >= 2, "the published message was never delivered/processed"
        # The storm property is NO SUSTAINED REDELIVERY (the held count stops
        # growing once acked) — not "exactly one delivery": Pub/Sub is
        # at-least-once, so a transport-level duplicate in the initial window is
        # legal and is NOT the storm (the pre-11a storm grew without bound).
        for _ in range(3):
            await subscriber.poll_once()
        assert _held_chunks(dis_admin, chunk.trace_id) == held, (
            "a quarantined (acked) message must NOT keep redelivering — the storm fix"
        )
    finally:
        drain_subscription(_PROJECT)

    with dis_admin.begin() as conn:
        held_rows = conn.execute(
            text(
                "SELECT status, failure_stage, failure_reason FROM quarantine.quarantined_chunks "
                "WHERE trace_id = CAST(:t AS uuid)"
            ),
            {"t": str(chunk.trace_id)},
        ).all()
        failure_audits = conn.execute(
            text(
                "SELECT COUNT(*) FROM audit.events WHERE trace_id = CAST(:t AS uuid) "
                "AND outcome = 'FAILURE' AND stage = 'PRE_MAPPING_VALIDATED'"
            ),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
        quarantined_audits = conn.execute(
            text(
                "SELECT COUNT(*) FROM audit.events WHERE trace_id = CAST(:t AS uuid) "
                "AND stage = 'QUARANTINED'"
            ),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
        sale_rows = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_sale_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
    # The chunk was processed at least twice (the routing call + the subscriber
    # pass); each pass holds it again (D44-style duplicate tolerance — no dedup
    # key on the store; the console reads status=NEW grouped by trace).
    assert held_rows and all(row.status == "NEW" for row in held_rows)
    assert all(row.failure_stage == "PRE_MAPPING_VALIDATION" for row in held_rows)
    assert all(row.failure_reason == "PRE_VALIDATION_FAILED" for row in held_rows)
    assert failure_audits >= 1  # the FAILURE record still emitted (audit posture unchanged)
    assert quarantined_audits >= 1  # the QUARANTINED disposition record
    assert sale_rows == 0  # never partially written


async def test_non_allowlisted_failure_still_nacks_and_redelivers(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
) -> None:
    """The allowlist is NARROW: the store-miss CONTRACT_VIOLATION (a self-heal
    case — mirror-sync lag is its designed recovery) keeps today's audit-and-nack
    and the emulator redelivers it."""
    unknown_store = UUID("019e9999-0000-7000-8000-00000000dead")  # not in identity_mirror
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=b"sold_at,sku,qty,retail,price,txn,line\n2026-01-01 10:00:00,X,1,9.99,8.50,T,1\n",
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
        event_store_uuid=unknown_store,
    )
    payload = chunk.event.model_dump_json(exclude_none=True).encode()

    decision = await process_message(pipeline, payload)
    assert decision == "nack"
    baseline = _contract_failure_audits(dis_admin, chunk.trace_id)  # the direct call's FAILURE
    assert baseline >= 1  # audit-and-nack unchanged

    # PER-TRACE redelivery proof (stray-immune, see the storm-fix test): every
    # delivery of OUR message emits one more CONTRACT_VIOLATION failure audit,
    # so a count of baseline+2 means first delivery AND at least one REdelivery.
    drain_subscription(_PROJECT)
    try:
        publisher = EmulatorPublisher(project_id=_PROJECT)
        publisher.publish(INGRESS_READY_TOPIC, payload)
        subscriber = Subscriber(project_id=_PROJECT, pipeline=pipeline, max_messages=10)
        deliveries = baseline
        for _ in range(15):
            await subscriber.poll_once()
            deliveries = _contract_failure_audits(dis_admin, chunk.trace_id)
            if deliveries >= baseline + 2:
                break
        assert deliveries >= baseline + 2, "the nacked message was never redelivered (silent drop?)"
    finally:
        drain_subscription(_PROJECT)

    with dis_admin.begin() as conn:
        held_rows = conn.execute(
            text("SELECT COUNT(*) FROM quarantine.quarantined_rows WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
    assert _held_chunks(dis_admin, chunk.trace_id) == 0  # carved out: NOT quarantined
    assert held_rows == 0


def test_quarantine_topic_publish_is_absent() -> None:
    # The 11a/11b boundary, review-pinned: the quarantine SINK exists (the direct
    # write into quarantine.*), but no `quarantine` TOPIC publish and no drainer —
    # the topic-mediated path is Slice 11b.
    import streaming_consumer.sinks.quarantine as quarantine_sink

    source = inspect.getsource(quarantine_sink)
    assert "pubsub" not in source.lower(), "11a writes the store directly; the topic is 11b"
    with pytest.raises(ImportError):
        __import__("streaming_consumer.sinks.dlq")
