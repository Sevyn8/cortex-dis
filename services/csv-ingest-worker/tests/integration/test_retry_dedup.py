"""The Slice 8 client-retry story, proven ACROSS the seam on the live stack.

dis-ui-server's tests prove a retry re-derives the same deterministic
``upload_session_id``; the worker's units prove the D58 dedup query. This module
proves the two actually compose: two real csv.received events shaped exactly as
a client retry produces them (same bytes → same payload_sha256, same
tenant/store/template → same upload_session_id, DIFFERENT trace_id and DIFFERENT
trace-keyed GCS object per attempt) run through the real pipeline against live
5433 — and exactly one bronze row and exactly one ingress.ready publish exist.

One publish total is also the D65 protection, structurally: an id-less source's
``source_event_id`` is ``bronze_ref:chunk_row_index``, so double-counted
canonical events require a second bronze row / second ingress.ready — which the
dedup never lets exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from csv_ingest_worker.audit import WorkerAudit
from csv_ingest_worker.envelope import CsvReceivedEvent
from csv_ingest_worker.pipeline import IngestPipeline
from dis_audit import AuditBackend, select_writer
from dis_core.ids import new_uuid7
from dis_core.timestamps import now_utc
from dis_storage import build_object_path
from dis_testing.fakes.pubsub import InMemoryPublisher
from dis_testing.fixtures import DEFAULT_SOURCE_ID, PRIMARY_STORE, PRIMARY_TENANT

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration

_RETRY_CSV = b"sku,store_section,qty_sold,unit_price\nR-1,front,2,5.00\nR-2,back,7,1.25\n"


def _attempt_event(
    storage: StorageClient,
    bucket: str,
    *,
    upload_session_id: str,
    template_id: UUID,
    cleanup: list[UUID],
) -> CsvReceivedEvent:
    """One client ATTEMPT, exactly as dis-ui-server produces it: a fresh trace_id
    and a fresh trace-keyed object, but the SAME deterministic lineage id."""
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
    storage.upload_bytes(key, _RETRY_CSV, content_type="text/csv")
    return CsvReceivedEvent(
        schema_version=1,
        trace_id=trace_id,
        tenant_id=PRIMARY_TENANT.uuid,
        store_id=PRIMARY_STORE.uuid,
        source_id=DEFAULT_SOURCE_ID,
        template_id=template_id,
        upload_session_id=upload_session_id,
        gcs_uri=f"gs://{bucket}/{key}",
        received_ts=received,
        tenant_display_code=PRIMARY_TENANT.display_code,
        store_code=PRIMARY_STORE.store_code,
    )


def _bronze_rows_for(dis_admin: Engine, upload_session_id: str) -> list[tuple[UUID, str]]:
    with dis_admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT trace_id, gcs_uri FROM bronze.data_ingress_events "
                "WHERE source_payload_id = :spid ORDER BY received_at"
            ),
            {"spid": upload_session_id},
        ).all()
    return [(r.trace_id, r.gcs_uri) for r in rows]


async def test_double_delivery_retry_yields_one_bronze_row_and_one_publish(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    # The retry where BOTH attempts published (e.g. the client timed out on a
    # slow 201 and re-posted): two events, same upload_session_id + same bytes.
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = InMemoryPublisher()
    pipeline = IngestPipeline(
        engine=engine,
        storage=storage,
        publisher=publisher,
        audit=WorkerAudit(select_writer(AuditBackend.POSTGRES, engine=engine)),
        bronze_bucket=bucket,
    )
    upload_session_id = f"us_{new_uuid7().hex[:12]}"  # fresh so the 24h window can't couple runs
    template_id = new_uuid7()

    first = _attempt_event(
        storage, bucket, upload_session_id=upload_session_id, template_id=template_id, cleanup=cleanup_traces
    )
    second = _attempt_event(
        storage, bucket, upload_session_id=upload_session_id, template_id=template_id, cleanup=cleanup_traces
    )
    assert first.trace_id != second.trace_id  # every attempt is its own trace/object

    one = await pipeline.process(first)
    two = await pipeline.process(second)

    assert one.disposition == "ingested"
    assert two.disposition == "duplicate_noop"  # the dedup FIRED across the seam
    assert two.trace_id == first.trace_id  # the prior attempt's trace is the answer

    rows = _bronze_rows_for(dis_admin, upload_session_id)
    assert len(rows) == 1, f"expected exactly one bronze row, found {len(rows)}"
    assert rows[0][0] == first.trace_id

    # Exactly ONE ingress.ready ever left this pipeline: the D65 structural
    # guarantee (no second bronze_ref can mint duplicate id-less source_event_ids
    # downstream).
    assert len(publisher.messages_for("ingress.ready")) == 1


async def test_orphan_then_retry_yields_one_bronze_row_referencing_the_retry_object(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    dis_admin: Engine,
    cleanup_traces: list[UUID],
) -> None:
    # The adversarial variant: attempt 1 wrote its GCS object but the publish
    # failed at dis-ui-server (the accepted orphan) — the worker NEVER sees an
    # event for it. The retry's event is the only one delivered.
    bucket = stack_env["GCS_BUCKET_BRONZE"]
    publisher = InMemoryPublisher()
    pipeline = IngestPipeline(
        engine=engine,
        storage=storage,
        publisher=publisher,
        audit=WorkerAudit(select_writer(AuditBackend.POSTGRES, engine=engine)),
        bronze_bucket=bucket,
    )
    upload_session_id = f"us_{new_uuid7().hex[:12]}"
    template_id = new_uuid7()

    # Attempt 1: object lands (the orphan), NO event is built or processed.
    orphan = _attempt_event(
        storage, bucket, upload_session_id=upload_session_id, template_id=template_id, cleanup=cleanup_traces
    )
    # Attempt 2 (the retry): same bytes, same lineage id, fresh trace + object.
    retry = _attempt_event(
        storage, bucket, upload_session_id=upload_session_id, template_id=template_id, cleanup=cleanup_traces
    )

    outcome = await pipeline.process(retry)

    assert outcome.disposition == "ingested"  # no prior bronze row — the orphan left none
    rows = _bronze_rows_for(dis_admin, upload_session_id)
    assert len(rows) == 1
    assert rows[0][0] == retry.trace_id
    assert rows[0][1] == retry.gcs_uri  # bronze references the RETRY's object…
    assert rows[0][1] != orphan.gcs_uri  # …never the orphan (which nothing references)
    assert len(publisher.messages_for("ingress.ready")) == 1  # one publish total (D65)
