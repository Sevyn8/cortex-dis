"""AC7 + D65: the D33 read-time latest-wins window over the D38 dedup key.

ERROR-not-skip with teeth: these queries name source_id/source_event_id — if the
M-D38/D64 migration were absent, every test here ERRORS on the missing columns.

- ``test_correction_is_latest_wins_survivor``: the same source event (same
  ``transaction_id:line_item_seq`` dedup key) corrected by the source — a NEW
  bronze object with a LATER source timestamp — yields two append-only rows
  (no UNIQUE, hard rule 7), and the window returns the correction as the
  survivor.
- ``test_idless_correction_documented`` (D65, the accepted-behavior proof): an
  id-less source (change events; the fallback ``bronze_ref:row_index`` key)
  re-uploads a correction as a GENUINELY NEW bronze object (distinct
  ``bronze_ref`` — execute-time item 2: NOT the same object re-published, which
  is the redelivery case and would correctly collapse). The two rows do NOT
  collapse at read (distinct dedup keys — the documented D65 limitation), while
  the hot table still converges to the later event via event-time-wins (D64).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_core.ids import new_uuid7
from dis_testing.fixtures import PRIMARY_TENANT
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

# The D33 window, mapped to the live columns (D38 resolution): event-time DESC,
# then write-time, then the uuidv7 id as the deterministic final tie-break.
_SALE_WINDOW = text(
    "SELECT source_event_id, quantity, trace_id FROM ("
    "  SELECT source_event_id, quantity, trace_id,"
    "         ROW_NUMBER() OVER ("
    "           PARTITION BY tenant_id, store_id, source_id, source_event_id"
    "           ORDER BY source_sale_timestamp DESC, last_updated_at DESC, id DESC) AS rn"
    "  FROM canonical.store_sku_sale_events"
    "  WHERE tenant_id = CAST(:tenant AS uuid) AND source_id = :source_id"
    "    AND trace_id = ANY(:traces)"
    ") latest WHERE rn = 1"
)


async def test_correction_is_latest_wins_survivor(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    sku = f"DD-{new_uuid7().hex[:10]}"
    txn = f"T-{new_uuid7().hex[:8]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])

    original = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku, "5", "9.99", "8.50", txn, "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    assert (await pipeline.process(original.event)).disposition == "written"

    # The source corrects the SAME event (same txn:line key): new bronze object,
    # later source timestamp, quantity 5 -> 3.
    correction = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(60), sku, "3", "9.99", "8.50", txn, "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    assert (await pipeline.process(correction.event)).disposition == "written"

    with dis_admin.begin() as conn:
        all_rows = conn.execute(
            text(
                "SELECT COUNT(*) FROM canonical.store_sku_sale_events "
                "WHERE source_event_id = :key AND tenant_id = CAST(:tenant AS uuid)"
            ),
            {"key": f"{txn}:1", "tenant": str(PRIMARY_TENANT.uuid)},
        ).scalar_one()
        survivors = conn.execute(
            _SALE_WINDOW,
            {
                "tenant": str(PRIMARY_TENANT.uuid),
                "source_id": SALE_SOURCE_ID,
                "traces": [original.trace_id, correction.trace_id],
            },
        ).all()

    assert all_rows == 2  # append-only: the correction did NOT overwrite (D33)
    assert len(survivors) == 1  # the window collapses the key to one survivor
    assert survivors[0].quantity == Decimal("3.000")  # ...the correction
    assert str(survivors[0].trace_id) == str(correction.trace_id)


async def test_idless_correction_documented(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    sku = f"D65-{new_uuid7().hex[:10]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=consumer_mappings[CHANGE_SOURCE_ID])

    original = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=change_csv([(ts(0), sku, "10")]),
        source_id=CHANGE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    assert (await pipeline.process(original.event)).disposition == "written"

    # The tenant re-uploads a corrected count: a genuinely NEW bronze object
    # (seed_chunk mints a fresh bronze_ref + trace), later source timestamp.
    correction = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=change_csv([(ts(90), sku, "12")]),
        source_id=CHANGE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    assert correction.bronze_ref != original.bronze_ref  # the D65 precondition
    assert (await pipeline.process(correction.event)).disposition == "written"

    with dis_admin.begin() as conn:
        keys = conn.execute(
            text(
                "SELECT source_event_id FROM ("
                "  SELECT source_event_id,"
                "         ROW_NUMBER() OVER ("
                "           PARTITION BY tenant_id, store_id, source_id, source_event_id"
                "           ORDER BY source_event_timestamp DESC, last_updated_at DESC, id DESC) AS rn"
                "  FROM canonical.store_sku_change_events"
                "  WHERE trace_id = ANY(:traces)"
                ") latest WHERE rn = 1"
            ),
            {"traces": [original.trace_id, correction.trace_id]},
        ).all()
        hot_stock = conn.execute(
            text(
                "SELECT stock_qty FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).scalar_one()

    # The documented D65 limitation: distinct bronze objects -> distinct fallback
    # dedup keys -> BOTH rows survive the read-time window (no correction-collapse
    # for id-less sources)...
    assert len(keys) == 2
    expected = {f"{original.bronze_ref}:0", f"{correction.bronze_ref}:0"}
    assert {k.source_event_id for k in keys} == expected
    # ...while current truth still converges via event-time-wins (D64).
    assert hot_stock == Decimal("12")
