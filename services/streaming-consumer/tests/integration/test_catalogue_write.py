"""Slice 14d: the catalogue (snapshot) bootstrap-CREATE write path.

Proofs (ERROR-not-skip; the conftest raises StackRequiredError when the stack is
absent):

- a catalogue chunk on a ``template_type='snapshot'`` mapping CREATEs a hot row
  with NO pre-seeded row (the complete-path INSERT; the event paths cannot create
  one), writing NOTHING to either event table;
- ``currency`` and ``tax_treatment`` are STORE-supplied via dis-enrichment (slice-5b,
  D95/D98): the lib's values WIN over the mapping (currency's file ``constant`` is
  overridden), both resolved from identity_mirror.stores;
- ``attribute_staleness_map`` is stamped for exactly the contendable attributes
  the row set, with the snapshot's event-time (the envelope ``received_ts``);
- the hot row carries the loaded mapping's ``mapping_version_id`` (D22) and the
  event's ``trace_id`` (read, never minted).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_testing.fixtures import PRIMARY_TENANT
from streaming_consumer.orchestrate import ConsumerPipeline

from .conftest import (
    CATALOGUE_MINIMAL_SOURCE_ID,
    CATALOGUE_SOURCE_ID,
    CHANGE_SOURCE_ID,
    SALE_SOURCE_ID,
    Cleanup,
    catalogue_csv,
    catalogue_minimal_csv,
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


async def test_catalogue_chunk_creates_hot_row_no_events(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    sku = _unique_sku("CAT")
    cleanup.skus.append(sku)
    # NO pre-seeded hot row: the catalogue path must CREATE it (the event paths
    # cannot — the incomplete path has no INSERT).
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=catalogue_csv([(sku, "Widget", "Hardware", "9.99", "4.00", "42")]),
        source_id=CATALOGUE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "written"
    assert outcome.report is not None
    assert outcome.report.event_rows_written == 0  # no event table written
    assert outcome.report.hot_rows_upserted == 1
    assert outcome.report.written_to_table == "canonical.store_sku_current_position"

    with dis_admin.begin() as conn:
        sale_rows = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_sale_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
        change_rows = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_change_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(chunk.trace_id)},
        ).scalar_one()
        hot = conn.execute(
            text(
                "SELECT mapping_version_id, trace_id, current_retail_price, unit_cost, stock_qty, "
                "currency, tax_treatment, last_source_event_at, attribute_staleness_map "
                "FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).one()

    # Catalogue path is hot-only: nothing in either event table.
    assert sale_rows == 0
    assert change_rows == 0

    # The bootstrap CREATE landed, version + trace stamped (D22 / hard rule 4).
    assert hot.mapping_version_id == consumer_mappings[CATALOGUE_SOURCE_ID]
    assert str(hot.trace_id) == str(chunk.trace_id)
    assert hot.current_retail_price == Decimal("9.9900")
    assert hot.unit_cost == Decimal("4.0000")
    assert hot.stock_qty == Decimal("42.000")

    # slice-5b (D95/D98): currency AND tax_treatment are STORE-supplied via enrichment
    # — the lib's values win over the file. Both equal identity_mirror.stores, and the
    # file's `constant 'EUR'` currency is overridden.
    with dis_admin.begin() as conn:
        store = conn.execute(
            text(
                "SELECT currency, tax_treatment FROM identity_mirror.stores "
                "WHERE tenant_id = CAST(:t AS uuid) AND store_id = CAST(:s AS uuid)"
            ),
            {"t": str(PRIMARY_TENANT.uuid), "s": str(chunk.event.store_id)},
        ).one()
    assert hot.currency == store.currency
    assert hot.tax_treatment == store.tax_treatment
    assert store.currency != "EUR", "seed store currency must differ from the file constant to prove override"

    # Event-time = the envelope received_ts; staleness stamped for exactly the
    # contendable attributes the row set, each with the event-time value. The row
    # set price, cost, stock_qty, product_name, currency (all contendable) and
    # product_category (NOT contendable → not stamped).
    assert hot.last_source_event_at is not None
    received_iso = chunk.event.received_ts.isoformat()
    stamp = hot.attribute_staleness_map
    assert set(stamp) == {"current_retail_price", "unit_cost", "stock_qty", "product_name", "currency"}
    assert set(stamp.values()) == {received_iso}
    assert "product_category" not in stamp  # no event mutates it


async def test_catalogue_snapshot_omitting_category_and_cost_lands_nulls(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    """Slice 16j headline: a snapshot mapping that omits BOTH unit_cost and
    product_category still classifies COMPLETE (they are nullable now, so they left the
    write-gate required set), upserts exactly one hot row, and that row carries both
    columns as NULL — read back from the DB, not merely classified."""
    sku = _unique_sku("CATMIN")
    cleanup.skus.append(sku)
    # NO pre-seeded hot row: the catalogue complete-path INSERT must create it, even
    # though the mapping never projects unit_cost or product_category.
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=catalogue_minimal_csv([(sku, "Gadget", "12.50", "7")]),
        source_id=CATALOGUE_MINIMAL_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )

    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "written"  # COMPLETE despite omitting both targets
    assert outcome.report is not None
    assert outcome.report.event_rows_written == 0
    assert outcome.report.hot_rows_upserted == 1
    assert outcome.report.written_to_table == "canonical.store_sku_current_position"

    with dis_admin.begin() as conn:
        hot = conn.execute(
            text(
                "SELECT product_category, unit_cost, current_retail_price, stock_qty, "
                "product_name, mapping_version_id "
                "FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).one()

    # The headline: both omitted columns landed NULL; the mapped columns landed values.
    assert hot.product_category is None
    assert hot.unit_cost is None
    assert hot.current_retail_price == Decimal("12.5000")
    assert hot.stock_qty == Decimal("7.000")
    assert hot.product_name == "Gadget"
    assert hot.mapping_version_id == consumer_mappings[CATALOGUE_MINIMAL_SOURCE_ID]


async def test_catalogue_currency_is_store_supplied_overriding_the_file(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    """slice-5b (D95, criterion 2): currency is STORE-supplied via enrichment — the
    lib's value WINS over the file's ``constant 'EUR'`` derive. Output-wins proven for
    a mapping-produced column at the integration grain."""
    sku = _unique_sku("CUR")
    cleanup.skus.append(sku)
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=catalogue_csv([(sku, "Gadget", "Electronics", "19.50", "11.00", "7")]),
        source_id=CATALOGUE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "written"
    with dis_admin.begin() as conn:
        store_currency = conn.execute(
            text(
                "SELECT currency FROM identity_mirror.stores "
                "WHERE tenant_id = CAST(:t AS uuid) AND store_id = CAST(:s AS uuid)"
            ),
            {"t": str(PRIMARY_TENANT.uuid), "s": str(chunk.event.store_id)},
        ).scalar_one()
        hot_currency = conn.execute(
            text(
                "SELECT currency FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND sku_id = :sku"
            ),
            {"tenant": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).scalar_one()
    # The store value wins; the file's constant 'EUR' is overridden (output-wins, D95).
    assert hot_currency == store_currency
    assert store_currency != "EUR", "seed store currency must differ from the file constant to prove override"


async def test_event_chunks_never_reach_the_catalogue_writer(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The additive boundary, directly: with write_catalogue_chunk booby-trapped to
    raise the instant it is called, a sale chunk AND a change chunk still process to
    completion — proving the type router never routes an event type into the
    catalogue writer. A catalogue chunk, by contrast, DOES reach it (the trap fires)."""
    import streaming_consumer.orchestrate as orch

    async def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("write_catalogue_chunk reached for a non-snapshot chunk")

    monkeypatch.setattr(orch, "write_catalogue_chunk", _boom)

    # Sale: pre-seed the hot row (incomplete path updates in place), process → written.
    sku_s = _unique_sku("NR-S")
    seed_hot_row(dis_admin, cleanup, sku_id=sku_s, mapping_version_id=consumer_mappings[SALE_SOURCE_ID])
    sale = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), sku_s, "1", "9.99", "8.50", "T-NR", "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    assert (await pipeline.process(sale.event)).disposition == "written"

    # Change: same — the trap must not fire.
    sku_c = _unique_sku("NR-C")
    seed_hot_row(dis_admin, cleanup, sku_id=sku_c, mapping_version_id=consumer_mappings[CHANGE_SOURCE_ID])
    change = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=change_csv([(ts(0), sku_c, "7")]),
        source_id=CHANGE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    assert (await pipeline.process(change.event)).disposition == "written"

    # And the catalogue path DOES reach the writer (the trap fires) — proving the
    # test would have caught a leak, not passed vacuously.
    sku_cat = _unique_sku("NR-CAT")
    cleanup.skus.append(sku_cat)
    cat = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=catalogue_csv([(sku_cat, "W", "H", "9.99", "4.00", "1")]),
        source_id=CATALOGUE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    with pytest.raises(AssertionError, match="write_catalogue_chunk reached"):
        await pipeline.process(cat.event)
