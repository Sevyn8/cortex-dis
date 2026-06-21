"""Wire models for ``GET /dashboard/metrics`` (the tenant Dashboard KPIs + Flow).

Read-only aggregates over existing tables (audit.events, quarantine.*, canonical.*),
all tenant-scoped through ``rls_session``. No new vocabulary, no writes.

The quarantine ``rate`` is an APPROXIMATION and is deliberately accompanied by the
raw numerators/denominators: a row quarantined inside the 24h window may belong to
an upload received before it, so this is a window-aligned ratio, not a cohort rate.
The UI leads with the raw count for honesty. ``rate`` is null when nothing was
received in the window (no denominator to divide by).
"""

from __future__ import annotations

from pydantic import BaseModel


class QuarantineMetrics(BaseModel):
    """24h quarantine numbers: the raw counts plus the approximate rate."""

    quarantined_rows: int
    received_rows: int
    rate: float | None  # quarantined_rows / received_rows; null when received_rows == 0


class CanonicalTableCount(BaseModel):
    """Row count for one canonical table (the mapping-produced ingest tables)."""

    table: str
    count: int


class CanonicalRecords(BaseModel):
    """Total canonical rows for the tenant, with a per-table breakdown."""

    total: int
    by_table: list[CanonicalTableCount]


class FlowRow(BaseModel):
    """Per-template recent ingest volume + last-received, for the Flow panel.

    Keyed by ``template_id`` (from the upload audit event); the UI resolves the
    display name / source from the templates it already lists.
    """

    template_id: str | None  # event_data->>'template_id'; null if an upload carried none
    rows_24h: int
    last_received_at: str | None  # ISO-8601, or null when no receipt in the window


class DashboardMetrics(BaseModel):
    """The full tenant Dashboard metrics payload (one read, one RLS session)."""

    rows_ingested_24h: int
    quarantine_24h: QuarantineMetrics
    records_in_canonical: CanonicalRecords
    flow: list[FlowRow]
