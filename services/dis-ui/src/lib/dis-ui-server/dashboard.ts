import { useQuery } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { getJson } from './client'
import { isRealMode } from './mode'

// Tenant Dashboard metrics (the KPI tiles + Flow), tenant slice. Shaped EXACTLY to the real
// dis-ui-server contract (services/dis-ui-server/.../schemas/dashboard.py:DashboardMetrics):
// GET /api/v1/dashboard/metrics, one tenant-scoped read over audit.events + quarantine.* +
// canonical.*. Mode-aware (T10): real mode calls the live endpoint; fixture mode (default +
// tests) returns plausible inlined numbers so local dev needs no backend.
//
// The earlier fabricated per-source rollup (DashboardSummary / useDashboardSummary) was removed
// when this real metrics endpoint landed: it was a fixture-only stand-in the screen never used.

// 24h quarantine numbers: the raw counts plus the approximate (window-aligned) rate. `rate` is
// null when nothing was received in the window. The UI leads with the raw count, not the rate.
export type QuarantineMetrics = {
  quarantined_rows: number
  received_rows: number
  rate: number | null
}

export type CanonicalTableCount = {
  table: string
  count: number
}

// Total canonical rows for the tenant (the mapping-produced ingest tables), with a per-table
// breakdown. signal_history is excluded server-side (derived daily-compute, not ingested rows).
export type CanonicalRecords = {
  total: number
  by_table: CanonicalTableCount[]
}

// Per-template recent ingest volume + last-received (the Flow panel). Keyed by template_id; the
// screen resolves the display name / source from the mapping-templates it already lists.
export type FlowRow = {
  template_id: string | null
  rows_24h: number
  last_received_at: string | null
}

export type DashboardMetrics = {
  rows_ingested_24h: number
  quarantine_24h: QuarantineMetrics
  records_in_canonical: CanonicalRecords
  flow: FlowRow[]
}

// Plausible fixture, grounded on the Sales fixture template id (mapping-templates.ts) so the
// Flow panel resolves a real name in fixture mode. Numbers are illustrative, not fabricated
// truth: fixture mode is local-dev/test only; real mode reads the live endpoint.
const FIXTURE_METRICS: DashboardMetrics = {
  rows_ingested_24h: 1247,
  quarantine_24h: { quarantined_rows: 3, received_rows: 1247, rate: 3 / 1247 },
  records_in_canonical: {
    total: 65,
    by_table: [
      { table: 'store_sku_current_position', count: 65 },
      { table: 'store_sku_sale_events', count: 0 },
      { table: 'store_sku_change_events', count: 0 },
    ],
  },
  flow: [
    {
      template_id: '0190ac10-5a00-7000-8a00-0000000000a1',
      rows_24h: 1247,
      last_received_at: '2026-06-09T09:12:00Z',
    },
  ],
}

// GET /api/v1/dashboard/metrics. Tenant-scoped server-side (token tenant only). Real mode calls
// the live endpoint; fixture mode returns the inlined metrics.
export async function getDashboardMetrics(): Promise<DashboardMetrics> {
  if (isRealMode()) {
    return getJson<DashboardMetrics>('/api/v1/dashboard/metrics')
  }
  return FIXTURE_METRICS
}

export function useDashboardMetrics(snapshot: AuthSnapshot | null) {
  return useQuery({
    queryKey: ['dis-ui-server', 'dashboard', 'metrics', snapshot?.tenantId ?? 'none'],
    queryFn: getDashboardMetrics,
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}
