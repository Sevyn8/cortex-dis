"""AC4: the no-orphan composite FK (D39), both ways, and RLS isolation.

ERROR-not-skip: conftest raises StackRequiredError when the stack is absent.

- Present ``(tenant_id, store_id)`` writes (the happy path, asserted again here).
- Absent pair fails LOUD at the write: a CHANGE chunk (no store-row data read on
  that path, so nothing fails earlier) for a fabricated store UUID dies on
  ``fk_ssce_store`` inside the transaction — zero canonical rows land.
- RLS isolation: rows written under tenant A are invisible to a
  tenant-B-scoped ``rls_session`` read and visible to a tenant-A-scoped one
  (hard rules 1 and 12) — proven through the SERVICE role, not the admin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_core.ids import new_uuid7
from dis_rls import rls_session
from dis_testing.fixtures import TENANTS
from streaming_consumer.orchestrate import ConsumerPipeline

from .conftest import CHANGE_SOURCE_ID, Cleanup, change_csv, seed_chunk, ts

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration


async def test_fk_no_orphan_both_ways(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
) -> None:
    sku = f"FK-{new_uuid7().hex[:10]}"
    cleanup.skus.append(sku)

    # Present pair: writes (change events have no hot-NOT-NULL dependency for an
    # unseen SKU? they do — but INVENTORY/stock_qty merges onto an EXISTING row;
    # an unseen SKU would D63-fail, so write the event for a seeded position).
    from .conftest import seed_hot_row

    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=1)
    good = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=change_csv([(ts(0), sku, "17")]),
        source_id=CHANGE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    outcome = await pipeline.process(good.event)
    assert outcome.disposition == "written"

    # Absent pair: the ENVELOPE names a store UUID identity_mirror does not hold
    # (a malformed producer — the bronze row keeps a valid store, since bronze
    # carries its own composite FK). The consumer trusts the event's identity
    # (D54), so the canonical composite FK (tenant_id, store_id) ->
    # identity_mirror.stores is the last line of defense and fails LOUD at the
    # write (D39).
    orphan_store = new_uuid7()
    bad = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=change_csv([(ts(5), sku, "18")]),
        source_id=CHANGE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
        event_store_uuid=orphan_store,
    )
    with pytest.raises(Exception):  # noqa: B017, PT011 - ForeignKeyViolation via the driver
        await pipeline.process(bad.event)

    with dis_admin.begin() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM canonical.store_sku_change_events WHERE trace_id = CAST(:t AS uuid)"),
            {"t": str(bad.trace_id)},
        ).scalar_one()
    assert count == 0  # no orphan row landed, either side


async def test_rls_isolation_between_tenants(
    pipeline: ConsumerPipeline,
    engine: AsyncEngine,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
) -> None:
    from .conftest import seed_hot_row

    sku = f"RLS-{new_uuid7().hex[:10]}"
    seed_hot_row(dis_admin, cleanup, sku_id=sku, mapping_version_id=1)
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=change_csv([(ts(0), sku, "5")]),
        source_id=CHANGE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )
    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "written"

    tenant_a = chunk.event.tenant_id
    tenant_b = next(t.uuid for t in TENANTS if t.uuid != tenant_a)
    query = text("SELECT COUNT(*) FROM canonical.store_sku_change_events WHERE trace_id = CAST(:t AS uuid)")
    params = {"t": str(chunk.trace_id)}

    async with rls_session(engine, tenant_a) as conn:
        visible_to_a = (await conn.execute(query, params)).scalar_one()
    async with rls_session(engine, tenant_b) as conn:
        visible_to_b = (await conn.execute(query, params)).scalar_one()

    assert visible_to_a == 1  # the owner sees the row through the service role
    assert visible_to_b == 0  # tenant B's scoped session sees NOTHING (RLS)
