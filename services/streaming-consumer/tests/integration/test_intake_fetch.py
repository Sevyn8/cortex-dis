"""AC2: live Pub/Sub delivery proof + the read-identity/read-trace discipline.

A test plays the 9b worker and publishes ``ingress.ready`` on the REAL emulator
topic; the consumer's subscriber pulls, processes, acks. The delivery proof
ERRORS, never skips: missing env raises StackRequiredError (conftest); an absent
subscription raises ``DisError`` at Subscriber construction (asserted positively
here); an absent topic raises NotFound from the publisher.

The trace discipline: every audit row and every canonical row of the run carries
the EVENT's trace_id (the consumer mints none, hard rule 4), and the consumer
makes no Identity Service call (``clients/identity.py`` does not exist — also a
review-only property, asserted as an import check here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_core.errors import DisError
from dis_core.ids import new_uuid7
from dis_testing.fakes.pubsub import EmulatorPublisher
from dis_testing.pubsub import pubsub_test_project
from streaming_consumer.clients.pubsub import Subscriber
from streaming_consumer.config import INGRESS_READY_TOPIC
from streaming_consumer.orchestrate import ConsumerPipeline

from .conftest import (
    SALE_SOURCE_ID,
    Cleanup,
    drain_subscription,
    sale_csv,
    seed_chunk,
    seed_hot_row,
    ts,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration

_PROJECT = pubsub_test_project()  # D100: tests run on a project residents never subscribe to


async def test_delivery_proof_and_trace_discipline(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    sku = f"IF-{new_uuid7().hex[:10]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "1", "9.99", "9.50", "T-IF", "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    # Publish on the real emulator topic; the subscriber pulls and processes.
    # Defensive drain first: a prior test's deliberately-nacked residue must not
    # starve this test's pulls (the audit-and-nack interim posture).
    drain_subscription(_PROJECT)
    publisher = EmulatorPublisher(project_id=_PROJECT)
    payload = chunk.event.model_dump_json(exclude_none=True).encode()
    publisher.publish(INGRESS_READY_TOPIC, payload)

    subscriber = Subscriber(project_id=_PROJECT, pipeline=pipeline, max_messages=10)
    handled = 0

    def _written() -> bool:
        with dis_admin.begin() as conn:
            return bool(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM canonical.store_sku_sale_events "
                        "WHERE trace_id = CAST(:t AS uuid)"
                    ),
                    {"t": str(chunk.trace_id)},
                ).scalar_one()
            )

    for _ in range(10):  # the emulator may deliver on a later pull
        handled += await subscriber.poll_once()
        if handled and _written():
            break
    assert handled >= 1, "the published ingress.ready never arrived (delivery proof)"
    assert _written(), "the delivered chunk never landed in canonical"

    with dis_admin.begin() as conn:
        canonical_traces = conn.execute(
            text(
                "SELECT DISTINCT trace_id FROM canonical.store_sku_sale_events "
                "WHERE trace_id = CAST(:t AS uuid)"
            ),
            {"t": str(chunk.trace_id)},
        ).all()
        audit_traces = conn.execute(
            text(
                "SELECT DISTINCT trace_id FROM audit.events "
                "WHERE trace_id = CAST(:t AS uuid) AND service_name = 'streaming-consumer'"
            ),
            {"t": str(chunk.trace_id)},
        ).all()
        foreign_audit = conn.execute(
            text(
                "SELECT COUNT(*) FROM audit.events "
                "WHERE data_ingress_event_id = CAST(:b AS uuid) "
                "AND trace_id <> CAST(:t AS uuid)"
            ),
            {"b": str(chunk.bronze_ref), "t": str(chunk.trace_id)},
        ).scalar_one()

    # The consumer emitted under the EVENT's trace_id and minted no other for
    # this run (every audit row referencing this bronze carries the read trace).
    assert len(canonical_traces) == 1
    assert len(audit_traces) >= 1
    assert foreign_audit == 0


def test_no_identity_client_exists() -> None:
    # AC2/D28: the consumer makes no Identity Service call — the module the
    # reserved tree holds for Slice 13 does not exist at all in Slice 10.
    with pytest.raises(ImportError):
        __import__("streaming_consumer.clients.identity")


def test_absent_subscription_errors_never_skips(
    pipeline: ConsumerPipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The delivery proof's ERROR-not-skip posture, asserted positively: a
    # subscriber over a non-provisioned subscription name refuses to start.
    import streaming_consumer.clients.pubsub as pubsub_module

    monkeypatch.setattr(pubsub_module, "INGRESS_READY_SUBSCRIPTION", f"absent-{new_uuid7().hex[:8]}")
    with pytest.raises(DisError, match="does not exist"):
        Subscriber(project_id=_PROJECT, pipeline=pipeline)
