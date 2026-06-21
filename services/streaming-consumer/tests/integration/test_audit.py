"""AC10: audit is fire-and-forget; the duplicate COLUMN representation is emitted.

- An injected audit-writer failure (raises on every write) does NOT stop the
  data path: the chunk still lands (logged, never raised — hard rule 11).
- The duplicate path (a redelivered chunk) emits ROW-scoped CANONICAL_WRITTEN
  events whose OUTCOME is the kind — ``DUPLICATE_NOOP``/``DUPLICATE_OVERWRITTEN``
  (refining SUCCESS: the append-only insert landed, D33) — with
  ``prior_trace_id`` as a COLUMN; ``row_hash`` and the dedup key stay in
  ``event_data``. This is Slice 30c's D42 REVISION (the Slice-10 JSONB shape
  superseded for console queryability) — the flipped assertions here are the
  deliberate change, not a regression.
- Duplicate audit rows are tolerated (D44): the second delivery re-emits the
  same stages under the same trace; both sets exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_audit import AuditEvent
from dis_core.ids import new_uuid7
from streaming_consumer.orchestrate import ConsumerPipeline
from streaming_consumer.sinks.audit import ConsumerAudit

from .conftest import SALE_SOURCE_ID, Cleanup, sale_csv, seed_chunk, seed_hot_row, ts

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration


class _ExplodingWriter:
    """An audit writer that always raises — the injected failure."""

    async def write(self, event: AuditEvent) -> bool:
        raise RuntimeError("injected audit backend failure")


async def test_audit_failure_never_blocks_the_data_path(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    pipeline.audit = ConsumerAudit(_ExplodingWriter())  # inject the failure
    sku = f"AU-{new_uuid7().hex[:10]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "1", "9.99", "9.50", "T-AU", "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    outcome = await pipeline.process(chunk.event)  # must NOT raise (hard rule 11)
    assert outcome.disposition == "written"

    with dis_admin.begin() as conn:
        rows = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_sale_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
    assert rows == 1  # the data path completed despite every audit write failing


async def test_duplicate_path_sets_outcome_and_prior_trace_columns(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    """FLIPPED by Slice 30c (the D42 revision): formerly asserted the Slice-10
    event_data-JSONB shape; now asserts the column promotion."""
    sku = f"AU-{new_uuid7().hex[:10]}"
    txn = f"T-{new_uuid7().hex[:8]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "2", "9.99", "8.50", txn, "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    assert (await pipeline.process(chunk.event)).disposition == "written"

    # Redelivery: identical payload -> DUPLICATE_NOOP under the same trace.
    assert (await pipeline.process(chunk.event)).disposition == "written"

    # A correction (same dedup key, different payload, new bronze) -> OVERWRITTEN.
    correction = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(45), sku, "3", "9.99", "8.50", txn, "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    assert (await pipeline.process(correction.event)).disposition == "written"

    with dis_admin.begin() as conn:
        duplicate_rows = (
            conn.execute(
                text(
                    "SELECT trace_id, outcome, prior_trace_id, event_data FROM audit.events "
                    "WHERE event_scope = 'ROW' AND stage = 'CANONICAL_WRITTEN' "
                    "AND outcome IN ('DUPLICATE_NOOP', 'DUPLICATE_OVERWRITTEN') "
                    "AND trace_id = ANY(:traces)"
                ),
                {"traces": [chunk.trace_id, correction.trace_id]},
            )
            .mappings()
            .all()
        )
        same_trace_stage_rows = conn.execute(
            text(
                "SELECT COUNT(*) FROM audit.events WHERE trace_id = CAST(:t AS uuid) "
                "AND stage = 'CANONICAL_WRITTEN' AND event_scope = 'INGRESS_EVENT'"
            ),
            {"t": str(chunk.trace_id)},
        ).scalar_one()

    by_kind = {row["outcome"]: row for row in duplicate_rows}
    assert "DUPLICATE_NOOP" in by_kind, "redelivery did not set the NOOP outcome column"
    assert "DUPLICATE_OVERWRITTEN" in by_kind, "the correction did not set OVERWRITTEN"

    noop = by_kind["DUPLICATE_NOOP"]
    assert str(noop["prior_trace_id"]) == str(chunk.trace_id)  # the COLUMN, not JSONB
    assert noop["event_data"]["dedup_key"]["source_event_id"] == f"{txn}:1"
    assert noop["event_data"]["dedup_key"]["source_id"] == SALE_SOURCE_ID
    assert "row_hash" in noop["event_data"]  # NOT promoted — stays in event_data
    # The old JSONB keys are GONE (promoted, not duplicated).
    assert "duplicate" not in noop["event_data"]
    assert "prior_trace_id" not in noop["event_data"]

    overwritten = by_kind["DUPLICATE_OVERWRITTEN"]
    assert str(overwritten["prior_trace_id"]) == str(chunk.trace_id)
    assert "duplicate" not in overwritten["event_data"]

    # D44: the redelivery re-emitted CANONICAL_WRITTEN under the same trace —
    # duplicate audit rows exist and are tolerated, not prevented.
    assert same_trace_stage_rows >= 2


async def test_redelivery_intake_is_retried_and_durations_populated(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    """Slice 30b: a redelivered chunk's intake is legible as RETRIED (best-effort
    audit readback), and every consumer-emitted row carries a non-negative
    duration_ms (the lap-timer seam)."""
    sku = f"AU-{new_uuid7().hex[:10]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "1", "9.99", "9.50", f"T-{new_uuid7().hex[:8]}", "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    assert (await pipeline.process(chunk.event)).disposition == "written"
    # Redelivery of the SAME envelope: the second intake must read as RETRIED.
    assert (await pipeline.process(chunk.event)).disposition == "written"

    with dis_admin.begin() as conn:
        received = (
            conn.execute(
                text(
                    "SELECT outcome FROM audit.events "
                    "WHERE trace_id = CAST(:t AS uuid) AND stage = 'RECEIVED' "
                    "AND service_name = 'streaming-consumer' ORDER BY _loaded_at, id"
                ),
                {"t": str(chunk.trace_id)},
            )
            .scalars()
            .all()
        )
        # duration_ms is the STAGE span (the lap-timer seam), so it lives on
        # INGRESS_EVENT-scoped rows; ROW-scoped records (per-row failures,
        # duplicate hits) are not stages and carry none (untouched until 30c).
        durations = (
            conn.execute(
                text(
                    "SELECT duration_ms FROM audit.events "
                    "WHERE trace_id = CAST(:t AS uuid) AND service_name = 'streaming-consumer' "
                    "AND event_scope = 'INGRESS_EVENT'"
                ),
                {"t": str(chunk.trace_id)},
            )
            .scalars()
            .all()
        )
    assert received == ["SUCCESS", "RETRIED"], (
        "the first delivery's intake is SUCCESS; the redelivery's must be RETRIED"
    )
    assert durations, "no consumer audit rows found"
    assert all(d is not None and d >= 0 for d in durations), "duration_ms must be populated, non-negative"


async def test_broken_readback_degrades_to_success_and_never_blocks_processing(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 30b RETRIED degradation + non-interference, at the PIPELINE level.

    ``rls_session`` is imported into orchestrate.py for exactly one call site —
    the ``_seen_before`` readback — so breaking THAT import breaks only the
    readback, leaving the data path (fetch/lookup/write use their own modules)
    intact. With the readback exploding on every call:

    - a first delivery still processes to 'written' and its intake emits
      SUCCESS (no wedge, no nack-shaped disposition);
    - a genuine REDELIVERY also processes and its intake degrades to SUCCESS —
      a missed RETRIED label, never an error, never RETRIED-by-accident.
    """

    def exploding_rls_session(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected readback outage (audit-side only)")

    monkeypatch.setattr("streaming_consumer.orchestrate.rls_session", exploding_rls_session)

    sku = f"AU-{new_uuid7().hex[:10]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "1", "9.99", "9.50", f"T-{new_uuid7().hex[:8]}", "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    # First delivery: processes normally despite the readback outage.
    assert (await pipeline.process(chunk.event)).disposition == "written"
    # Genuine redelivery with the readback still broken: degrades, still written.
    assert (await pipeline.process(chunk.event)).disposition == "written"

    with dis_admin.begin() as conn:
        received = (
            conn.execute(
                text(
                    "SELECT outcome FROM audit.events "
                    "WHERE trace_id = CAST(:t AS uuid) AND stage = 'RECEIVED' "
                    "AND service_name = 'streaming-consumer' ORDER BY _loaded_at, id"
                ),
                {"t": str(chunk.trace_id)},
            )
            .scalars()
            .all()
        )
    assert received == ["SUCCESS", "SUCCESS"], (
        "a broken readback must degrade BOTH intakes to SUCCESS (a missed "
        "RETRIED label), never RETRIED-by-accident and never a failure"
    )
