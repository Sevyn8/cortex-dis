import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearToken, writeToken } from '../../auth/storage'
import { getDashboardMetrics } from './dashboard'

// Dashboard metrics (GET /dashboard/metrics). Fixture mode returns plausible inlined numbers;
// real mode hits the live endpoint. Shapes mirror the dis-ui-server DashboardMetrics contract.

describe('dashboard metrics (fixture mode)', () => {
  it('returns the KPI + flow shape with non-negative numbers', async () => {
    const m = await getDashboardMetrics()
    expect(typeof m.rows_ingested_24h).toBe('number')
    expect(m.rows_ingested_24h).toBeGreaterThanOrEqual(0)

    // quarantine: raw counts present; rate is null or a number
    expect(m.quarantine_24h.quarantined_rows).toBeGreaterThanOrEqual(0)
    expect(m.quarantine_24h.received_rows).toBeGreaterThanOrEqual(0)
    expect(m.quarantine_24h.rate === null || typeof m.quarantine_24h.rate === 'number').toBe(true)

    // canonical: total equals the sum of the per-table breakdown
    const sum = m.records_in_canonical.by_table.reduce((acc, t) => acc + t.count, 0)
    expect(m.records_in_canonical.total).toBe(sum)
    expect(m.records_in_canonical.by_table.map((t) => t.table)).toEqual([
      'store_sku_current_position',
      'store_sku_sale_events',
      'store_sku_change_events',
    ])

    // flow: a per-template row keyed by a real fixture template id
    expect(m.flow.length).toBeGreaterThan(0)
    expect(m.flow[0].template_id).toBe('0190ac10-5a00-7000-8a00-0000000000a1')
    expect(m.flow[0].rows_24h).toBeGreaterThanOrEqual(0)
  })
})

describe('dashboard metrics real mode (T10)', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_DIS_UI_SERVER_MODE', 'real')
    vi.stubEnv('VITE_DIS_UI_SERVER_BASE_URL', 'http://test.local')
    writeToken('tok-123')
  })
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    clearToken()
  })

  it('GETs /api/v1/dashboard/metrics with a Bearer and returns the payload', async () => {
    const payload = {
      rows_ingested_24h: 10,
      quarantine_24h: { quarantined_rows: 1, received_rows: 10, rate: 0.1 },
      records_in_canonical: {
        total: 5,
        by_table: [{ table: 'store_sku_current_position', count: 5 }],
      },
      flow: [{ template_id: 't-1', rows_24h: 10, last_received_at: '2026-06-09T09:00:00Z' }],
    }
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () => ({ ok: true, status: 200, json: async () => payload }) as unknown as Response,
    )
    vi.stubGlobal('fetch', fetchMock)

    const m = await getDashboardMetrics()
    expect(m).toEqual(payload)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/dashboard/metrics')
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok-123' })
  })
})
