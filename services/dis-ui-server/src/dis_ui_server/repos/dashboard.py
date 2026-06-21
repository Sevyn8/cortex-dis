"""Dashboard metric reads - pure tenant-scoped SELECTs over existing tables.

Every statement runs inside ONE ``rls_session(engine, tenant_id)`` so the per-tenant
GUC (``app.tenant_id``) scopes the audit / quarantine / canonical reads exactly like
the other tenant-facing read repos (mapping-templates, stores). Read-only: no writes,
no DDL, no business logic beyond aggregation. Canonical reads go through the dis-rls
helper (hard rule 1).

Sources:
- ``audit.events`` RECEIVED/SUCCESS rows carry the upload's ``row_count`` (the CSV
  upload receiver emits one per accepted upload) and ``event_data->>'template_id'``.
- ``quarantine.quarantined_rows`` / ``quarantined_chunks`` carry ``quarantined_at``.
- ``canonical.*`` are the mapping-produced ingest tables (signal_history is derived
  daily-compute output, NOT ingested records, so it is excluded from the count).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_ui_server.auth.scope import ReadScope
from dis_ui_server.db import read_session

# The canonical tables that hold mapping-produced ingest rows. store_sku_signal_history
# is daily-compute DERIVED output (not ingested records), so it is deliberately excluded.
# Static literals (no user input) - safe to interpolate into the count statement.
_CANONICAL_TABLES: tuple[str, ...] = (
    "store_sku_current_position",
    "store_sku_sale_events",
    "store_sku_change_events",
)

# COUNT(*) on the append-only event tables is acceptable at beta scale (~150K/day); a
# cached/approximate count is a later optimization if these grow large.

_ROWS_INGESTED_24H = text(
    "SELECT COALESCE(SUM(row_count), 0) AS n FROM audit.events "
    "WHERE stage = 'RECEIVED' AND outcome = 'SUCCESS' "
    "AND event_timestamp >= now() - interval '24 hours'"
)

_QUARANTINED_24H = text(
    "SELECT "
    "(SELECT count(*) FROM quarantine.quarantined_rows "
    " WHERE quarantined_at >= now() - interval '24 hours') "
    "+ "
    "(SELECT count(*) FROM quarantine.quarantined_chunks "
    " WHERE quarantined_at >= now() - interval '24 hours') AS n"
)

_FLOW_24H = text(
    "SELECT event_data->>'template_id' AS template_id, "
    "COALESCE(SUM(row_count), 0) AS rows_24h, "
    "MAX(event_timestamp) AS last_received_at "
    "FROM audit.events "
    "WHERE stage = 'RECEIVED' AND outcome = 'SUCCESS' "
    "AND event_timestamp >= now() - interval '24 hours' "
    "GROUP BY 1 ORDER BY rows_24h DESC"
)


@dataclass(frozen=True)
class FlowAggRow:
    """One template's 24h ingest aggregate (raw, pre-wire)."""

    template_id: str | None
    rows_24h: int
    last_received_at: datetime | None


@dataclass(frozen=True)
class DashboardMetricsData:
    """The raw metric values read from the DB; the handler maps these to the wire."""

    rows_ingested_24h: int
    quarantined_rows_24h: int
    canonical_by_table: list[tuple[str, int]]
    flow: list[FlowAggRow]


async def fetch_dashboard_metrics(engine: AsyncEngine, scope: ReadScope) -> DashboardMetricsData:
    """Read every Dashboard metric in one scoped session (Slice 17b).

    ``scope`` comes from ``require_read_scope`` (verified token only): a TENANT scope
    pins ``app.tenant_id``; a PLATFORM scope reads see-all (aggregates span every tenant)
    via the policy USING branch. All reads are scoped by RLS — no in-query predicate.
    """
    async with read_session(engine, is_platform=scope.is_platform, tenant_id=scope.tenant_id) as conn:
        rows_ingested = int((await conn.execute(_ROWS_INGESTED_24H)).scalar_one())
        quarantined = int((await conn.execute(_QUARANTINED_24H)).scalar_one())

        canonical_by_table: list[tuple[str, int]] = []
        for table in _CANONICAL_TABLES:
            count = int(
                (await conn.execute(text(f"SELECT count(*) AS n FROM canonical.{table}"))).scalar_one()
            )
            canonical_by_table.append((table, count))

        flow = [
            FlowAggRow(
                template_id=row.template_id,
                rows_24h=int(row.rows_24h),
                last_received_at=row.last_received_at,
            )
            for row in (await conn.execute(_FLOW_24H)).all()
        ]

    return DashboardMetricsData(
        rows_ingested_24h=rows_ingested,
        quarantined_rows_24h=quarantined,
        canonical_by_table=canonical_by_table,
        flow=flow,
    )
