import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearToken, writeToken } from '../../auth/storage'
import { CATALOG_FIXTURE } from './mapping-fields'
import { getMappingSuggestions, mechanicalSuggest } from './mapping-suggestions'
import type { ColumnProfile } from './mapping-suggestions'

const COLUMNS: ColumnProfile[] = [
  { name: 'qty', inferred_datatype: 'integer', null_pct: 0, sample_values: ['12'] },
  { name: 'item_code', inferred_datatype: 'text', null_pct: 0, sample_values: ['A123'] },
]

describe('mechanicalSuggest (fixture stand-in, ports the server fallback matcher)', () => {
  it('maps common retail columns to catalog keys, source = fallback', () => {
    const res = mechanicalSuggest(COLUMNS, CATALOG_FIXTURE)
    expect(res.source).toBe('fallback')
    expect(res.model).toBeNull()
    const byCol = Object.fromEntries(res.suggestions.map((s) => [s.source_column, s]))
    expect(byCol.qty.suggested_target).toBe('quantity')
    expect(byCol.qty.confidence).toBeGreaterThanOrEqual(0.7)
    expect(byCol.item_code.suggested_target).toBe('sku_id')
  })

  it('an unknown column gets low confidence but a real catalog key', () => {
    const res = mechanicalSuggest(
      [{ name: 'zzz_unrelated', inferred_datatype: 'text', null_pct: 0, sample_values: ['x'] }],
      CATALOG_FIXTURE,
    )
    expect(res.suggestions[0].confidence).toBeLessThan(0.5)
    expect(CATALOG_FIXTURE.map((f) => f.key)).toContain(res.suggestions[0].suggested_target)
  })
})

describe('getMappingSuggestions fixture mode', () => {
  it('returns the mechanical stand-in (source fallback) with no backend', async () => {
    const res = await getMappingSuggestions(
      { columns: COLUMNS, template_type: 'sales' },
      CATALOG_FIXTURE,
    )
    expect(res.source).toBe('fallback')
    expect(res.suggestions).toHaveLength(2)
  })
})

describe('getMappingSuggestions real mode (mocked fetch)', () => {
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

  it('POSTs the profile to /api/v1/mapping-suggestions with a Bearer and parses the response', async () => {
    const RESP = {
      source: 'llm',
      model: 'gemini-2.5-flash',
      suggestions: [{ source_column: 'qty', suggested_target: 'quantity', confidence: 0.9 }],
    }
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () => ({ ok: true, status: 200, json: async () => RESP }) as unknown as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    const res = await getMappingSuggestions(
      {
        columns: COLUMNS,
        source_id: 'manual_csv_upload',
        template_name: 'Sales',
        template_type: 'sales',
      },
      CATALOG_FIXTURE,
    )
    expect(res.source).toBe('llm')
    expect(res.model).toBe('gemini-2.5-flash')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/mapping-suggestions')
    expect((init as RequestInit).method).toBe('POST')
    expect((init as RequestInit).headers).toMatchObject({
      authorization: 'Bearer tok-123',
      'content-type': 'application/json',
    })
    const body = JSON.parse((init as RequestInit).body as string)
    expect(body.columns).toHaveLength(2)
    expect(body.source_id).toBe('manual_csv_upload')
  })
})
