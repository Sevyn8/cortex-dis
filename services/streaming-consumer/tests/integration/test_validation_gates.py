"""AC5: both validation gates, both ways — and the D13 posture (11a disposition).

- Pre-mapping (source-shape) REJECTS a structurally wrong chunk (a required
  source column absent) and ACCEPTS a well-formed one.
- Post-mapping (canonical-shape) REJECTS a contribution that maps to an invalid
  canonical frame (the bad-subtype mapping derives ``event_subtype='GIFT'``,
  off the model's enum vocab) and ACCEPTS a valid one.
- Gate failures are data-deterministic, so since Slice 11a they take the
  QUARANTINED disposition (the subscriber ACKS — the storm fix): a row-less
  failure shape (the absent column) is held at CHUNK grain in
  ``quarantined_chunks``; a row-indexed shape (the GIFT subtype, per-row enum
  check) is held in ``quarantined_rows``. Either way: ZERO canonical rows (the
  whole-chunk model is unchanged — no partial success) and the FAILURE audit
  rows still land (D13: the consumer is where semantic validation lives — the
  receiver stayed permissive).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_core.ids import new_uuid7
from streaming_consumer.orchestrate import ConsumerPipeline

from .conftest import (
    BAD_SUBTYPE_SOURCE_ID,
    SALE_SOURCE_ID,
    Cleanup,
    sale_csv,
    seed_chunk,
    seed_hot_row,
    ts,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration


def _no_canonical_rows(dis_admin: Engine, trace_id: object) -> bool:
    with dis_admin.begin() as conn:
        sale = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_sale_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(trace_id)},
        ).scalar_one()
        change = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_change_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(trace_id)},
        ).scalar_one()
    return int(sale) == 0 and int(change) == 0


def _failure_audit_rows(dis_admin: Engine, trace_id: object, stage: str) -> int:
    with dis_admin.begin() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM audit.events WHERE trace_id = CAST(:t AS uuid) "
                    "AND stage = :stage AND outcome = 'FAILURE'"
                ),
                {"t": str(trace_id), "stage": stage},
            ).scalar_one()
        )


def _assert_gate_failure_shape(dis_admin: Engine, trace_id: object, stage: str, summary_code: str) -> None:
    """The Slice 30b failure-audit shape at a validation gate: a stable
    summary code, ROW rows coded VALIDATION_ROW_FAILED with the pandera check
    in event_data, and both correlation ids populated on every row."""
    with dis_admin.begin() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT event_scope, failure_code, event_data, "
                    "data_ingress_event_id, mapping_version_id, duration_ms "
                    "FROM audit.events WHERE trace_id = CAST(:t AS uuid) "
                    "AND stage = :stage AND outcome = 'FAILURE'"
                ),
                {"t": str(trace_id), "stage": stage},
            )
            .mappings()
            .all()
        )
    summaries = [r for r in rows if r["event_scope"] == "INGRESS_EVENT"]
    row_scoped = [r for r in rows if r["event_scope"] == "ROW"]
    assert summaries and all(r["failure_code"] == summary_code for r in summaries)
    assert row_scoped, "gate failures must carry per-row ROW events"
    for r in row_scoped:
        assert r["failure_code"] == "VALIDATION_ROW_FAILED"
        assert r["event_data"] is not None and "check" in r["event_data"]
    for r in rows:
        assert r["data_ingress_event_id"] is not None
        assert r["mapping_version_id"] is not None


async def test_pre_validation_rejects_structurally_wrong_chunk(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
) -> None:
    # The sale mapping requires sold_at/sku/qty/retail/price/txn/line; this chunk
    # is missing 'qty' entirely — structural drift, caught PRE-mapping (D62
    # reactive posture).
    bad_csv = b"sold_at,sku,retail,price,txn,line\n2026-01-01 10:00:00,X,9.99,8.50,T,1\n"
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=bad_csv,
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "quarantined"
    assert _no_canonical_rows(dis_admin, chunk.trace_id)
    assert _failure_audit_rows(dis_admin, chunk.trace_id, "PRE_MAPPING_VALIDATED") >= 1
    _assert_gate_failure_shape(dis_admin, chunk.trace_id, "PRE_MAPPING_VALIDATED", "PRE_VALIDATION_FAILED")
    # The row-less failure shape (column absent — no row_offset exists) is held
    # at CHUNK grain under the gate-summary code.
    with dis_admin.begin() as conn:
        held = conn.execute(
            text(
                "SELECT status, failure_stage, failure_reason "
                "FROM quarantine.quarantined_chunks WHERE trace_id = CAST(:t AS uuid)"
            ),
            {"t": str(chunk.trace_id)},
        ).one()
    assert (held.status, held.failure_stage, held.failure_reason) == (
        "NEW",
        "PRE_MAPPING_VALIDATION",
        "PRE_VALIDATION_FAILED",
    )


async def test_pre_validation_accepts_well_formed_chunk(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    sku = f"VG-{new_uuid7().hex[:10]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "1", "9.99", "9.99", "T-VG", "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "written"


async def test_post_validation_rejects_invalid_canonical_frame(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
) -> None:
    # The bad-subtype mapping derives event_subtype='GIFT' — structurally a fine
    # chunk (passes pre), but the canonical frame violates the model's enum
    # vocab (SALE/RETURN/VOID) at the post gate.
    sku = f"VG-{new_uuid7().hex[:10]}"
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "1", "9.99", "8.99", "T-VB", "1")]),
        source_id=BAD_SUBTYPE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "quarantined"
    assert _no_canonical_rows(dis_admin, chunk.trace_id)
    assert _failure_audit_rows(dis_admin, chunk.trace_id, "POST_MAPPING_VALIDATED") >= 1
    _assert_gate_failure_shape(dis_admin, chunk.trace_id, "POST_MAPPING_VALIDATED", "POST_VALIDATION_FAILED")
    # The row-indexed failure shape (a per-row enum check) is held per ROW.
    with dis_admin.begin() as conn:
        held = conn.execute(
            text(
                "SELECT status, failure_stage, failure_reason, row_offset, mapping_version_id "
                "FROM quarantine.quarantined_rows WHERE trace_id = CAST(:t AS uuid)"
            ),
            {"t": str(chunk.trace_id)},
        ).all()
    assert held, "the failing rows must be held in quarantined_rows"
    for row in held:
        assert row.status == "NEW"
        assert row.failure_stage == "POST_MAPPING_VALIDATION"
        assert row.failure_reason == "VALIDATION_ROW_FAILED"
        assert row.row_offset >= 0
        assert row.mapping_version_id is not None  # NOT NULL + FK on the live table
