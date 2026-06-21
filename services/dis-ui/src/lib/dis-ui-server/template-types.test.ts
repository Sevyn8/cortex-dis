import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearToken, writeToken } from '../../auth/storage'
import { TEMPLATE_TYPES_FIXTURE, getTemplateTypes } from './template-types'

// Chunk 2 WIRED endpoint GET /api/v1/template-types. Fixture-backed by default; real mode hits
// the live endpoint. Shaped to schemas/template_types.py:TemplateType (key/display_name/description).
describe('template-types (fixture shaped to the real contract)', () => {
  it('every fixture entry has key/display_name/description and a known key', async () => {
    const known = ['sales', 'inventory_change', 'snapshot']
    for (const t of TEMPLATE_TYPES_FIXTURE) {
      expect(typeof t.key).toBe('string')
      expect(typeof t.display_name).toBe('string')
      expect(typeof t.description).toBe('string')
      expect(known).toContain(t.key)
    }
  })

  it('offers the three packet axes', async () => {
    const keys = TEMPLATE_TYPES_FIXTURE.map((t) => t.key)
    expect(keys).toEqual(['sales', 'inventory_change', 'snapshot'])
  })

  it('getter returns a copy of the fixture (fixture mode)', async () => {
    const result = await getTemplateTypes()
    expect(result).toHaveLength(TEMPLATE_TYPES_FIXTURE.length)
    expect(result).not.toBe(TEMPLATE_TYPES_FIXTURE)
  })
})

describe('template-types real mode (Chunk 2)', () => {
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

  it('GETs /api/v1/template-types with a Bearer and parses the list', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () =>
        ({
          ok: true,
          status: 200,
          json: async () => TEMPLATE_TYPES_FIXTURE,
        }) as unknown as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    const types = await getTemplateTypes()
    expect(types).toEqual(TEMPLATE_TYPES_FIXTURE)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/template-types')
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok-123' })
  })
})
