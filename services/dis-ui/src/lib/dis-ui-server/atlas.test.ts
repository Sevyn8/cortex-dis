import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createDraft, getDraft, listDrafts, patchDraft, publishDraft } from './atlas'
import type { AtlasDraft, DraftSummary } from './atlas'

// atlas.ts mirrors the existing client seam: fixture mode (default) returns contract-shaped
// stand-ins so local dev + the manual walkthrough run with no backend; real mode performs the
// live HTTP calls (asserted against a mocked fetch, like csv-uploads.test).

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('atlas client - fixture mode (default)', () => {
  it('listDrafts returns lean DraftSummary rows with exactly the wire keys', async () => {
    const rows = await listDrafts()
    expect(rows.length).toBeGreaterThan(0)
    const keys = Object.keys(rows[0]).sort()
    expect(keys).toEqual(
      [
        'created_at',
        'draft_id',
        'published_at',
        'schema_version',
        'status',
        'table_key',
        'updated_at',
        'vertical',
      ].sort(),
    )
    // No IR document on the lean list.
    expect((rows[0] as Record<string, unknown>).table).toBeUndefined()
  })

  it('listDrafts applies the status filter', async () => {
    const published = await listDrafts('published')
    expect(published.every((r: DraftSummary) => r.status === 'published')).toBe(true)
  })

  it('getDraft returns a contract-shaped AtlasDraft (curated_bearing + origin on the wire)', async () => {
    const d: AtlasDraft = await getDraft('draft-fixture-0001')
    expect(d.table.semantics).toBe('merge_upsert')
    const curated = d.table.fields.find((f) => f.curated_bearing)
    expect(curated).toBeDefined()
    // origin and curated_bearing are present per field (the grid reads them off the wire).
    for (const f of d.table.fields) {
      expect(typeof f.curated_bearing).toBe('boolean')
      expect(['inferred', 'human', null]).toContain(f.origin)
    }
  })

  it('patchDraft flips the edited field origin to human (server simulation)', async () => {
    const updated = await patchDraft('draft-fixture-0001', {
      fields: [{ name: 'on_hand_qty', mandatory: true, origin: 'human' }],
    })
    const edited = updated.table.fields.find((f) => f.name === 'on_hand_qty')
    expect(edited?.origin).toBe('human')
    expect(edited?.mandatory).toBe(true)
  })

  it('publishDraft returns a PublishReceipt', async () => {
    const receipt = await publishDraft('draft-fixture-0001')
    expect(receipt.status).toBe('published')
    expect(typeof receipt.audit_emitted).toBe('boolean')
  })
})

describe('atlas client - real mode (mocked fetch)', () => {
  function okResponse(body: unknown): Response {
    return { ok: true, status: 200, json: async () => body } as unknown as Response
  }

  beforeEach(() => {
    vi.stubEnv('VITE_DIS_UI_SERVER_MODE', 'real')
    vi.stubEnv('VITE_DIS_UI_SERVER_BASE_URL', 'http://test.local')
    localStorage.setItem('dis-ui.dev.authToken', 'tok-123')
  })

  it('listDrafts GETs /api/v1/atlas/drafts and passes ?status=', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      okResponse([]),
    )
    vi.stubGlobal('fetch', fetchMock)
    await listDrafts('draft')
    expect(fetchMock.mock.calls[0][0]).toBe('http://test.local/api/v1/atlas/drafts?status=draft')
  })

  it('createDraft POSTs multipart files[] + table_key to the vertical draft endpoint', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      okResponse({ draft_id: 'd1' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const f1 = new File(['a,b\n1,2\n'], 'one.csv', { type: 'text/csv' })
    const f2 = new File(['a,b\n3,4\n'], 'two.csv', { type: 'text/csv' })
    await createDraft('pharma', [f1, f2], 'rx_snapshot')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/atlas/verticals/pharma/draft')
    expect(init.method).toBe('POST')
    const form = init.body as FormData
    expect(form.getAll('files')).toHaveLength(2)
    expect(form.get('table_key')).toBe('rx_snapshot')
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer tok-123')
  })

  it('patchDraft PATCHes /api/v1/atlas/drafts/{id} and publishDraft POSTs /publish', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      okResponse({ draft_id: 'd1', table: { fields: [] } }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await patchDraft('d1', { fields: [{ name: 'x', origin: 'human' }] })
    expect(fetchMock.mock.calls[0][0]).toBe('http://test.local/api/v1/atlas/drafts/d1')
    expect(fetchMock.mock.calls[0][1].method).toBe('PATCH')

    fetchMock.mockClear()
    fetchMock.mockResolvedValue(okResponse({ status: 'published' }))
    await publishDraft('d1')
    expect(fetchMock.mock.calls[0][0]).toBe('http://test.local/api/v1/atlas/drafts/d1/publish')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
  })
})
