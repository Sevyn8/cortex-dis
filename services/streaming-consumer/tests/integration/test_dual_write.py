"""AC3: the atomic dual-write — both land in one transaction, or neither.

Proofs (ERROR-not-skip; the conftest raises StackRequiredError when the stack is
absent):

- a valid sale chunk lands the event rows AND the hot upsert (pre-seeded hot row,
  D63: catalogue-before-sales), all stamped with the loaded mapping's
  ``mapping_version_id`` (D22) and the event's ``trace_id`` (hard rule 4);
- a valid CHANGE chunk lands its event rows in ``store_sku_change_events`` (and
  nothing in the sale table — routing), UPDATES the pre-seeded hot row IN PLACE
  (the incomplete-mapping hot path has no INSERT; event-time-wins picks the
  later count), and the message acks;
- DIRECTION 1: an induced failure in the EVENT executemany (the live
  ``ck_ssse_unit_sale_price_le_retail`` CHECK — passes both suites, which
  deliberately re-author no value-relation invariants) rolls back BOTH sides;
- DIRECTION 2: an induced GENUINE HOT-side failure AFTER the event rows landed
  in the same transaction (a negative stock count: the change-event table has
  NO CHECK on ``numeric_value_after`` — introspected — so it passes both suites
  AND the event insert, then violates the live ``ck_sscp_stock_qty_non_negative``
  at the hot merge) rolls back BOTH sides too. DISTINCT from the revised-D63
  miss-after-commit (which deliberately COMMITS the event rows): this is an
  in-transaction statement ERROR, not a defined miss disposition;
- REVISED D63: a first-seen SKU on an incomplete mapping is a miss — event
  history RETAINED, loud raise after commit (the non-rollback contrast).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_testing.fixtures import PRIMARY_TENANT
from streaming_consumer.clients.pubsub import process_message
from streaming_consumer.orchestrate import ConsumerPipeline

from .conftest import (
    CHANGE_SOURCE_ID,
    SALE_SOURCE_ID,
    Cleanup,
    change_csv,
    sale_csv,
    seed_chunk,
    seed_hot_row,
    ts,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration


def _unique_sku(prefix: str) -> str:
    from dis_core.ids import new_uuid7

    return f"{prefix}-{new_uuid7().hex[:10]}"


async def test_atomic_dual_write_and_version_stamp(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    sku = _unique_sku("DW")
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv(
            [
                (ts(0), sku, "2", "9.99", "8.50", "T-A", "1"),
                (ts(30), sku, "1", "10.99", "10.50", "T-A", "2"),
            ]
        ),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "written"
    assert outcome.report is not None and outcome.report.event_rows_written == 2

    with dis_admin.begin() as conn:
        events = conn.execute(
            text(
                "SELECT mapping_version_id, trace_id, source_id, source_event_id, "
                "unit_retail_price FROM canonical.store_sku_sale_events "
                "WHERE trace_id = CAST(:t AS uuid) ORDER BY source_event_id"
            ),
            {"t": str(chunk.trace_id)},
        ).all()
        hot = conn.execute(
            text(
                "SELECT mapping_version_id, trace_id, current_retail_price, currency, "
                "last_source_event_at FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).one()

    # Both sides landed; every produced row carries the loaded mapping's version
    # (D22) and the event's trace_id (read, never minted).
    assert len(events) == 2
    expected_version = consumer_mappings[SALE_SOURCE_ID]
    assert all(e.mapping_version_id == expected_version for e in events)
    assert all(str(e.trace_id) == str(chunk.trace_id) for e in events)
    assert {e.source_event_id for e in events} == {"T-A:1", "T-A:2"}
    assert hot.mapping_version_id == expected_version
    assert str(hot.trace_id) == str(chunk.trace_id)
    # Column-scoped, event-time-wins within the chunk: the later row's price won.
    assert hot.current_retail_price == Decimal("10.9900")
    assert hot.last_source_event_at is not None


async def test_change_event_chunk_writes_canonical_updates_hot_in_place_and_acks(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    """The change-path happy case: event rows land in ``store_sku_change_events``
    (version-stamped, trace read not minted, the mapping's INVENTORY/COUNT/stock_qty
    constants materialized), the pre-seeded hot row is UPDATED IN PLACE — the
    incomplete-mapping hot path has no INSERT, and event-time-wins makes the later
    count the survivor — nothing routes to the sale table, and the message acks."""
    sku = _unique_sku("CH")
    seeded_hot_id = seed_hot_row(
        dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[CHANGE_SOURCE_ID]
    )
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=change_csv([(ts(0), sku, "7"), (ts(30), sku, "5")]),
        source_id=CHANGE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    decision = await process_message(pipeline, chunk.event.model_dump_json(exclude_none=True).encode())
    assert decision == "ack", "a fully written change chunk must ack"

    with dis_admin.begin() as conn:
        events = conn.execute(
            text(
                "SELECT mapping_version_id, trace_id, event_category, event_subtype, "
                "attribute_name, value_after, source_event_id "
                "FROM canonical.store_sku_change_events WHERE trace_id = CAST(:t AS uuid) "
                "ORDER BY source_event_timestamp"
            ),
            {"t": str(chunk.trace_id)},
        ).all()
        sale_rows = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_sale_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
        hot = conn.execute(
            text(
                "SELECT id, stock_qty, mapping_version_id, trace_id, last_source_event_at "
                "FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).all()

    # Event side: both rows, version-stamped (D22), the event's trace (hard rule 4),
    # the change-path id-less dedup fallback bronze_ref:row_index (D65).
    expected_version = consumer_mappings[CHANGE_SOURCE_ID]
    assert len(events) == 2
    assert all(e.mapping_version_id == expected_version for e in events)
    assert all(str(e.trace_id) == str(chunk.trace_id) for e in events)
    assert all(
        (e.event_category, e.event_subtype, e.attribute_name) == ("INVENTORY", "COUNT", "stock_qty")
        for e in events
    )
    assert [e.value_after for e in events] == ["7", "5"]
    assert {e.source_event_id for e in events} == {f"{chunk.bronze_ref}:0", f"{chunk.bronze_ref}:1"}

    # Routing: nothing leaked into the sale-event table.
    assert sale_rows == 0

    # Hot side, the incomplete-mapping contract: the seeded row updated IN PLACE
    # (same id, no second row — no INSERT exists on this path), the LATER count
    # won (event-time-wins, column-scoped), version + trace restamped.
    assert len(hot) == 1
    assert str(hot[0].id) == str(seeded_hot_id)
    assert hot[0].stock_qty == 5
    assert hot[0].mapping_version_id == expected_version
    assert str(hot[0].trace_id) == str(chunk.trace_id)
    assert hot[0].last_source_event_at is not None


async def test_mid_transaction_failure_rolls_back_both(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    sku = _unique_sku("RB")
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    # Last row: sale price ABOVE retail — passes both suites (no value-relation
    # checks re-authored) and violates the live CHECK during the event-table
    # executemany, the FIRST write of the batch transaction (statement order:
    # duplicate-detect SELECT → event executemany → hot merges). The batch rolls
    # back whole: no event rows, hot untouched. NOTE (REVISED D63): a first-seen
    # SKU is deliberately NOT a rollback case any more (the miss commits the
    # batch and raises after — event history retained; see
    # test_first_seen_sku_quarantines_loud_event_history_retained). A GENUINE
    # hot-side error inside the transaction (CHECK/infra) still rolls
    # both sides back via the shared rls_session.
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv(
            [
                (ts(0), sku, "2", "9.99", "8.50", "T-B", "1"),
                (ts(5), sku, "1", "9.99", "99.99", "T-B", "2"),
            ]
        ),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    with pytest.raises(Exception):  # noqa: B017, PT011 - IntegrityError via the driver stack
        await pipeline.process(chunk.event)

    with dis_admin.begin() as conn:
        event_count = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_sale_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
        hot = conn.execute(
            text(
                "SELECT current_retail_price, trace_id FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).one()

    # Either-or-neither at the batch grain: NO event rows, hot row untouched
    # (still the seeded values — the in-transaction upsert rolled back too).
    assert event_count == 0
    assert hot.current_retail_price == Decimal("1.0000")
    assert str(hot.trace_id) != str(chunk.trace_id)


async def test_first_seen_sku_quarantines_loud_event_history_retained(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
) -> None:
    """REVISED D63 (completeness-gated creation): a first-seen SKU on an
    INCOMPLETE-mapping chunk raises LOUDLY toward quarantine, writes ZERO hot
    rows, issues NO INSERT — and the event row IS PRESENT in the event table:
    the batch transaction commits the appended history before the miss raises
    (history retained; redelivery re-appends and read-time dedup absorbs).
    """
    sku = _unique_sku("D63")
    cleanup.skus.append(sku)  # defensive: no hot row should land
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "1", "9.99", "8.99", "T-D63", "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    with pytest.raises(Exception, match="first-seen SKU|completeness-gated|RETAINED"):
        await pipeline.process(chunk.event)

    with dis_admin.begin() as conn:
        event_count = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_sale_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
        hot_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).scalar_one()
        # Slice 30b failure-audit shape: the D63 miss lands as a stable,
        # correlated FAILURE — code HOT_POSITION_MISSING (not an exception class
        # name), with BOTH correlation ids the catch-all knows at the write stage.
        d63_failure = conn.execute(
            text(
                "SELECT failure_code, data_ingress_event_id, mapping_version_id "
                "FROM audit.events WHERE trace_id = CAST(:t AS uuid) "
                "AND stage = 'CANONICAL_WRITTEN' AND outcome = 'FAILURE'"
            ),
            {"t": str(chunk.trace_id)},
        ).one()
    assert event_count == 1  # history RETAINED: the appended event row committed
    assert hot_count == 0  # no hot row invented; no INSERT exists on this path
    assert d63_failure.failure_code == "HOT_POSITION_MISSING"
    assert d63_failure.data_ingress_event_id is not None
    assert d63_failure.mapping_version_id is not None


async def test_genuine_hot_failure_rolls_back_both_direction_two(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    """Direction-2 either-or-neither (D30): a GENUINE in-transaction hot-side
    failure AFTER the event rows landed rolls back BOTH sides.

    The inducement: a change chunk counting stock at -5. It passes the
    source-shape suite (no value checks), the engine (value_after stays a
    string), the canonical-shape suite (value_after is the Any/jsonb column),
    and the EVENT insert (canonical.store_sku_change_events carries NO CHECK on
    numeric_value_after — introspected, the lever this test rests on). The hot
    merge then sets stock_qty = -5 on the seeded row and violates the live
    ck_sscp_stock_qty_non_negative INSIDE the batch transaction — an ERROR, so
    the rls_session rolls the WHOLE batch back: the already-inserted event rows
    vanish and the hot row keeps its prior state. DISTINCT from the revised-D63
    miss path, which deliberately commits the event rows before raising.
    """
    sku = _unique_sku("D30-2")
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[CHANGE_SOURCE_ID])
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=change_csv([(ts(0), sku, "-5")]),
        source_id=CHANGE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    with pytest.raises(Exception, match="stock_qty_non_negative|check constraint"):
        await pipeline.process(chunk.event)

    with dis_admin.begin() as conn:
        event_count = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_change_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
        hot = conn.execute(
            text(
                "SELECT stock_qty, trace_id FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).one()

    assert event_count == 0  # the in-transaction event rows rolled back with the hot failure
    assert hot.stock_qty is None  # the seeded row untouched (seed leaves stock_qty NULL)
    assert str(hot.trace_id) != str(chunk.trace_id)  # no stamp landed either


# test_nnd_unique_index_enforces_on_null_segments RETIRED with its premise: the
# NND index and the read-modify-write upsert are gone (M-HOTKEY/0004; the
# completeness-gated two-path merge is concurrency-proven by
# test_concurrent_upsert.py).
