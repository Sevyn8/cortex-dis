"""Upsert-outcome classification and per-tenant count aggregation (pure)."""

from __future__ import annotations

from uuid import UUID

from mirror_sync_consumer.sinks.postgres import (
    SyncResult,
    TenantSyncCounts,
    UpsertCounts,
    _classify,
)


def test_classify_no_row_is_unchanged() -> None:
    assert _classify(None) == "unchanged"


def test_classify_xmax_zero_is_inserted() -> None:
    assert _classify((True,)) == "inserted"  # type: ignore[arg-type]


def test_classify_xmax_nonzero_is_updated() -> None:
    assert _classify((False,)) == "updated"  # type: ignore[arg-type]


def test_upsert_counts_record_and_seen() -> None:
    counts = UpsertCounts()
    counts.record("inserted")
    counts.record("updated")
    counts.record("updated")
    counts.record("unchanged")
    assert (counts.inserted, counts.updated, counts.unchanged) == (1, 2, 1)
    assert counts.seen == 4


def test_totals_aggregate_across_tenants() -> None:
    a = TenantSyncCounts(
        tenant_id=UUID(int=1),
        tenants=UpsertCounts(inserted=1),
        stores=UpsertCounts(inserted=2, updated=1),
    )
    b = TenantSyncCounts(
        tenant_id=UUID(int=2),
        tenants=UpsertCounts(updated=1),
        stores=UpsertCounts(unchanged=3),
    )
    result = SyncResult(per_tenant=[a, b])
    tenant_totals, store_totals = result.totals()
    assert (tenant_totals.inserted, tenant_totals.updated) == (1, 1)
    assert (store_totals.inserted, store_totals.updated, store_totals.unchanged) == (2, 1, 3)
