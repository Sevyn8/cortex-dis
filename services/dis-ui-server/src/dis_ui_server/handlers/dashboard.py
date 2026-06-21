"""``GET /dashboard/metrics`` - the tenant Dashboard's KPI + Flow reads (read-only).

Tenant from the verified token ONLY (no path/query parameter exists). The read goes
through ``repos/dashboard.py``, which opens one ``rls_session`` and runs pure
tenant-scoped aggregate SELECTs over audit.events, quarantine.*, and canonical.*.
No writes, no schema changes, no business logic beyond aggregation.

The quarantine block carries the raw counts (quarantined / received) alongside an
approximate ``rate`` (null when nothing was received in the window); the UI leads
with the raw count because the ratio is window-aligned, not a cohort rate.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_ui_server.auth.scope import ReadScope, require_read_scope
from dis_ui_server.repos.dashboard import fetch_dashboard_metrics
from dis_ui_server.schemas.dashboard import (
    CanonicalRecords,
    CanonicalTableCount,
    DashboardMetrics,
    FlowRow,
    QuarantineMetrics,
)

router = APIRouter()


@router.get("/dashboard/metrics")
async def get_dashboard_metrics(
    request: Request,
    scope: Annotated[ReadScope, Depends(require_read_scope)],
) -> DashboardMetrics:
    """Dashboard metrics: 24h ingest, quarantine, canonical, flow. TENANT sees its own;
    PLATFORM (user_type=PLATFORM + dis:ops) sees cross-tenant aggregates."""
    engine: AsyncEngine = request.app.state.engine
    data = await fetch_dashboard_metrics(engine, scope)

    received = data.rows_ingested_24h
    quarantined = data.quarantined_rows_24h
    # Approximate, window-aligned ratio; null when there is no denominator (no ingest).
    rate = (quarantined / received) if received > 0 else None

    return DashboardMetrics(
        rows_ingested_24h=data.rows_ingested_24h,
        quarantine_24h=QuarantineMetrics(
            quarantined_rows=quarantined,
            received_rows=received,
            rate=rate,
        ),
        records_in_canonical=CanonicalRecords(
            total=sum(count for _, count in data.canonical_by_table),
            by_table=[
                CanonicalTableCount(table=table, count=count) for table, count in data.canonical_by_table
            ],
        ),
        flow=[
            FlowRow(
                template_id=row.template_id,
                rows_24h=row.rows_24h,
                last_received_at=(
                    row.last_received_at.isoformat() if row.last_received_at is not None else None
                ),
            )
            for row in data.flow
        ],
    )
