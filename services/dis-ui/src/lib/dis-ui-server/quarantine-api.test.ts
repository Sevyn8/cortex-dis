import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import type { ReactNode } from 'react'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { clearToken, writeToken } from '../../auth/storage'
import { getQuarantineDetail, getQuarantineList, useQuarantineList } from './quarantine-api'

// Tenant Quarantine reads (GET /quarantine[/{item_id}]). Fixture mode applies the filters
// client-side and serves inlined items; real mode builds the query string + path and GETs.

describe('quarantine-api fixture mode', () => {
  it('lists held items with the filter-independent open count', async () => {
    const res = await getQuarantineList({})
    expect(res.open_count).toBe(3)
    expect(res.items.length).toBe(3)
    // every held item carries the type-tagged id + the wire fields
    expect(res.items[0].id).toMatch(/^(row|chunk):/)
  })

  it('filters by source, error type, and status (resolved yields nothing)', async () => {
    expect((await getQuarantineList({ source: 'shopify_pos_v2' })).items).toHaveLength(1)
    expect((await getQuarantineList({ errorType: 'normalization' })).items).toHaveLength(1)
    const open = await getQuarantineList({ status: 'open' })
    expect(open.items.length).toBe(3)
    // resolved has no producing path (D82) -> empty, but open_count stays filter-independent
    const resolved = await getQuarantineList({ status: 'resolved' })
    expect(resolved.items).toHaveLength(0)
    expect(resolved.open_count).toBe(3)
  })

  it('fetches a detail by its type-tagged id; original_payload is always null', async () => {
    const detail = await getQuarantineDetail('row:0190ac0e-1a01-7001-8a01-000000000001')
    expect(detail.id).toBe('row:0190ac0e-1a01-7001-8a01-000000000001')
    expect(detail.original_payload).toBeNull()
    expect(detail.chain_depth).toBe(0)
    expect(detail.mapping_version).toBe(1)
    // a chunk pre-lookup failure carries no mapping version
    const chunk = await getQuarantineDetail('chunk:0190ac0e-1a01-7001-8a01-000000000003')
    expect(chunk.mapping_version).toBeNull()
  })
})

describe('quarantine-api real mode (mocked fetch)', () => {
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

  it('GETs /quarantine with the four filters as query params + a Bearer', async () => {
    const payload = { items: [], open_count: 0 }
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () => ({ ok: true, status: 200, json: async () => payload }) as unknown as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    await getQuarantineList({
      source: 'manual_csv_upload',
      errorType: 'canonical-shape',
      status: 'open',
      window: '24h',
    })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(
      'http://test.local/api/v1/quarantine?source=manual_csv_upload&error_type=canonical-shape&status=open&window=24h',
    )
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok-123' })
  })

  it('GETs /quarantine/{item_id} with the type-tagged id encoded in the path', async () => {
    const detail = {
      id: 'row:abc',
      kind: 'row',
      trace_id: 't',
      source: 's',
      failed_at: '2026-06-09T09:00:00Z',
      mapping_version: 1,
      error_reason: 'POST_VALIDATION_FAILED',
      failure_stage: 'canonical-shape',
      error_context: 'x',
      original_payload: null,
      chain_depth: 0,
    }
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () => ({ ok: true, status: 200, json: async () => detail }) as unknown as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    await getQuarantineDetail('row:0190ac0e-1a01-7001-8a01-000000000001')
    const [url] = fetchMock.mock.calls[0]
    // the ':' in the type-tagged id is percent-encoded in the path
    expect(url).toBe(
      'http://test.local/api/v1/quarantine/row%3A0190ac0e-1a01-7001-8a01-000000000001',
    )
  })
})

// Regression pin: the EXACT real 200 body observed in staging (chunk row, microsecond+Z
// timestamp, the list-only field set - no detail-only fields) must flow through the fetcher AND
// the hook without throwing. getJson returns the body verbatim (no envelope, no validation, no
// field renaming), and QuarantineListResponse matches {items, open_count} field-for-field.
const REAL_200_BODY = {
  items: [
    {
      id: 'chunk:019eabff-1111-7000-8000-000000000001',
      kind: 'chunk',
      trace_id: '019eabde-2222-7000-8000-000000000001',
      source_id: 'wsp',
      source: 'wsp',
      error_reason: 'PRE_VALIDATION_FAILED',
      failure_stage: 'source-shape',
      failed_at: '2026-06-09T10:49:00.371792Z',
      status: 'open',
    },
    {
      id: 'row:019eabff-1111-7000-8000-000000000002',
      kind: 'row',
      trace_id: '019eabde-2222-7000-8000-000000000002',
      source_id: 'wsp',
      source: 'wsp',
      error_reason: 'POST_VALIDATION_FAILED',
      failure_stage: 'canonical-shape',
      failed_at: '2026-06-09T10:48:00.000000Z',
      status: 'open',
    },
  ],
  open_count: 3,
}

describe('quarantine-api real 200 body (staging regression pin)', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_DIS_UI_SERVER_MODE', 'real')
    vi.stubEnv('VITE_DIS_UI_SERVER_BASE_URL', 'http://test.local')
    writeToken('tok-real')
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          ({ ok: true, status: 200, json: async () => REAL_200_BODY }) as unknown as Response,
      ),
    )
  })
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    clearToken()
  })

  it('getQuarantineList parses the exact real response (no throw, verbatim shape)', async () => {
    const res = await getQuarantineList({})
    expect(res.open_count).toBe(3)
    expect(res.items).toHaveLength(2)
    expect(res.items[0].id).toBe('chunk:019eabff-1111-7000-8000-000000000001')
    expect(res.items[0].failure_stage).toBe('source-shape')
    expect(res.items[0].error_reason).toBe('PRE_VALIDATION_FAILED')
  })

  it('useQuarantineList resolves to success (not error) on the exact real response', async () => {
    const snapshot: AuthSnapshot = {
      userId: 'u1',
      tenantId: 't_acme9k2l1mn4',
      storeId: 's1',
      roles: ['dis:read'],
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children)
    const { result } = renderHook(() => useQuarantineList(snapshot, {}), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.isError).toBe(false)
    expect(result.current.data?.open_count).toBe(3)
    expect(result.current.data?.items).toHaveLength(2)
  })
})
