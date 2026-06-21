"""Write tenants and stores into identity_mirror, one transaction per tenant.

The write goes through ``dis-rls`` ``rls_session(engine, tenant_id)``, which on first use
asserts ``current_database()=='ithina_dis_db'`` and a NOBYPASSRLS role — so the DIS-side
target safety is inherited. ``identity_mirror`` is RLS-off (D41), so the per-tenant
``app.tenant_id`` scope is a harmless no-op; the guard is the reason we use it.

Per tenant, in one transaction: upsert the tenant row, then upsert that tenant's stores
(parent before children satisfies ``fk_ims_tenant``; atomic per tenant). Never deletes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Row, TextClause
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from dis_rls import rls_session
from mirror_sync_consumer.pull.reader import CmStore, CmTenant
from mirror_sync_consumer.sync.stores import STORE_UPSERT, store_params
from mirror_sync_consumer.sync.tenants import TENANT_UPSERT, tenant_params


@dataclass
class UpsertCounts:
    """How an entity's rows landed across one sync pass."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    def record(self, outcome: str) -> None:
        if outcome == "inserted":
            self.inserted += 1
        elif outcome == "updated":
            self.updated += 1
        elif outcome == "unchanged":
            self.unchanged += 1
        else:  # pragma: no cover - guarded by _classify's closed return set
            raise ValueError(f"unknown upsert outcome {outcome!r}")

    @property
    def seen(self) -> int:
        return self.inserted + self.updated + self.unchanged


@dataclass
class TenantSyncCounts:
    """Per-tenant outcome: the tenant row plus its stores."""

    tenant_id: UUID
    tenants: UpsertCounts
    stores: UpsertCounts


@dataclass
class SyncResult:
    """The whole run's outcome, for run-boundary and per-tenant log lines."""

    per_tenant: list[TenantSyncCounts] = field(default_factory=list)
    skipped_stores: int = 0  # stores whose tenant CM did not return (CM FK makes this ~impossible)

    def totals(self) -> tuple[UpsertCounts, UpsertCounts]:
        tenants = UpsertCounts()
        stores = UpsertCounts()
        for pt in self.per_tenant:
            tenants.inserted += pt.tenants.inserted
            tenants.updated += pt.tenants.updated
            tenants.unchanged += pt.tenants.unchanged
            stores.inserted += pt.stores.inserted
            stores.updated += pt.stores.updated
            stores.unchanged += pt.stores.unchanged
        return tenants, stores


def _classify(row: Row[Any] | None) -> str:
    """Map a RETURNING ``(xmax = 0)`` row to inserted/updated/unchanged.

    No row -> the conditional WHERE excluded the update -> unchanged. The single column
    is ``(xmax = 0)``: true -> a fresh insert; false -> an in-place update.
    """
    if row is None:
        return "unchanged"
    return "inserted" if row[0] else "updated"


async def _apply(conn: AsyncConnection, stmt: TextClause, params: dict[str, Any]) -> str:
    result = await conn.execute(stmt, params)
    return _classify(result.first())


async def upsert_identity(
    write_engine: AsyncEngine,
    tenants: list[CmTenant],
    stores: list[CmStore],
    *,
    trace_id: UUID,
) -> SyncResult:
    """Upsert all tenants and their stores, one transaction per tenant. Never deletes."""
    stores_by_tenant: dict[UUID, list[CmStore]] = defaultdict(list)
    for store in stores:
        stores_by_tenant[store.tenant_id].append(store)
    tenant_ids = {t.tenant_id for t in tenants}

    result = SyncResult()
    for tenant in tenants:
        tenant_counts = UpsertCounts()
        store_counts = UpsertCounts()
        async with rls_session(write_engine, tenant.tenant_id) as conn:
            tenant_counts.record(await _apply(conn, TENANT_UPSERT, tenant_params(tenant)))
            for store in stores_by_tenant.get(tenant.tenant_id, []):
                store_counts.record(await _apply(conn, STORE_UPSERT, store_params(store)))
        result.per_tenant.append(
            TenantSyncCounts(tenant_id=tenant.tenant_id, tenants=tenant_counts, stores=store_counts)
        )

    result.skipped_stores = sum(len(v) for tid, v in stores_by_tenant.items() if tid not in tenant_ids)
    return result
