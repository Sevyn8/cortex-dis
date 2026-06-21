import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearToken, writeToken } from '../../auth/storage'
import {
  analyzeCsvSample,
  createConnectorSource,
  createCsvTemplate,
  exchangeToken,
  fetchLocations,
  fetchMappingSuggestions,
  fetchPreviewRows,
  fetchTemplateType,
  initiateOAuth,
  submitApiToken,
} from './connectors-api'
import type { CatalogField } from './mapping-fields'
import { CATALOG_FIXTURE } from './mapping-fields'
import { parseCsvFile } from '../onboarding/analyze-csv'
import { getMappingSuggestions } from './mapping-suggestions'

// Mock the two reused libs the REAL analyzeCsvSample composes (parse + type-aware suggestions),
// so the composition is asserted in isolation (no real papaparse run, no real POST).
vi.mock('../onboarding/analyze-csv', async (orig) => ({
  ...(await orig<typeof import('../onboarding/analyze-csv')>()),
  parseCsvFile: vi.fn(),
}))
vi.mock('./mapping-suggestions', async (orig) => ({
  ...(await orig<typeof import('./mapping-suggestions')>()),
  getMappingSuggestions: vi.fn(),
}))

// The connectors-api is the connectors seam: the POS paths + the CSV preview are STUBBED; the
// CSV analyze + create are REAL (compose existing endpoints). These assertions pin the shapes
// the step components rely on, and the real CSV wiring (D89/D90).

describe('connectors-api stub seam', () => {
  it('initiateOAuth returns an authorize URL + state', async () => {
    const init = await initiateOAuth('shopify', { shop_domain: 'acme.myshopify.com' })
    expect(init.authorizeUrl).toContain('acme.myshopify.com')
    expect(init.state).toContain('shopify')
  })

  it('exchangeToken and submitApiToken both return a read-only, token-stored account', async () => {
    const viaOauth = await exchangeToken('square', { state: 's' })
    const viaToken = await submitApiToken('clover', { api_token: 't' })
    for (const account of [viaOauth, viaToken]) {
      expect(account.readOnly).toBe(true)
      expect(account.tokenStored).toBe(true)
      expect(account.businessName.length).toBeGreaterThan(0)
    }
  })

  it('fetchLocations returns a non-empty location list', async () => {
    const locs = await fetchLocations('shopify')
    expect(locs.length).toBeGreaterThan(0)
    expect(locs[0]).toHaveProperty('id')
    expect(locs[0]).toHaveProperty('name')
  })

  it('mapping suggestions are shaped like the real response and target real catalog keys', async () => {
    const resp = await fetchMappingSuggestions('shopify', ['orders'])
    expect(resp.source).toBe('vertex')
    expect(resp.fields.length).toBeGreaterThan(0)
    const catalogKeys = new Set(CATALOG_FIXTURE.map((f) => f.key))
    for (const field of resp.fields) {
      expect(typeof field.confidence).toBe('number')
      expect('reasoning' in field).toBe(true)
      expect('detectedFormat' in field).toBe(true)
      // a suggested target is either null (unmapped) or a real catalog key
      if (field.suggestedTarget !== null) {
        expect(catalogKeys.has(field.suggestedTarget)).toBe(true)
      }
    }
  })

  it('fetchTemplateType returns a backend-provided value + label', async () => {
    const t = await fetchTemplateType()
    expect(t.value.length).toBeGreaterThan(0)
    expect(t.label.length).toBeGreaterThan(0)
  })

  it('fetchPreviewRows returns canonical-keyed rows', async () => {
    const rows = await fetchPreviewRows()
    expect(rows.length).toBeGreaterThan(0)
    expect(rows[0]).toHaveProperty('sku_id')
  })

  it('createConnectorSource returns a live source echoing the input', async () => {
    const source = await createConnectorSource({
      connector: 'shopify',
      sourceName: 'Shop sales',
      authMethod: 'oauth',
      locationIds: ['loc_001'],
      dataTypes: ['orders'],
      cadence: 'daily',
      ignoredFields: [],
      templateType: 'sale_event',
    })
    expect(source.connector).toBe('shopify')
    expect(source.sourceName).toBe('Shop sales')
    expect(source.status).toBe('live')
  })
})

describe('analyzeCsvSample (REAL: parse + type-aware suggestions, D90)', () => {
  afterEach(() => vi.clearAllMocks())

  const CATALOG: CatalogField[] = [
    {
      key: 'sku_id',
      display_name: 'SKU',
      section: 'sale_event',
      mandatory: true,
      constraints: null,
      datatype: 'text',
      description: '',
      allowed_values: null,
      max_length: 128,
      sink: 'store_sku_sale_event',
    },
    {
      key: '__ignore__',
      display_name: 'Ignore this column',
      section: 'system',
      mandatory: false,
      constraints: null,
      datatype: null,
      description: '',
      allowed_values: null,
      max_length: null,
      sink: null,
    },
  ]

  it('parses the file, passes template_type to the suggester, and maps the response', async () => {
    vi.mocked(parseCsvFile).mockResolvedValue({
      columns: [
        { name: 'item_code', inferred_datatype: 'text', null_pct: 0, sample_values: ['SKU-1'] },
        { name: 'note', inferred_datatype: 'text', null_pct: 0, sample_values: ['x'] },
      ],
      sample_rows: [{ item_code: 'SKU-1', note: 'x' }],
      row_count: 1,
    })
    vi.mocked(getMappingSuggestions).mockResolvedValue({
      source: 'llm',
      model: 'gemini-2.5-flash',
      suggestions: [
        {
          source_column: 'item_code',
          suggested_target: 'sku_id',
          confidence: 0.96,
          reasoning: 'matches SKU',
          alternatives: ['sku_variant'],
        },
        { source_column: 'note', suggested_target: null, confidence: 0.1 },
      ],
    })

    const file = new File(['item_code,note\nSKU-1,x'], 'sales.csv', { type: 'text/csv' })
    const result = await analyzeCsvSample(file, 'sales', CATALOG)

    // the suggester got the parsed columns AND the chosen template_type
    expect(vi.mocked(getMappingSuggestions).mock.calls[0][0]).toMatchObject({
      template_type: 'sales',
      columns: [{ name: 'item_code' }, { name: 'note' }],
    })
    // "llm" maps to the surface's "vertex"
    expect(result.source).toBe('vertex')
    // suggestions mapped to ConnectorMappingField[], sampleValues from the parsed profile
    expect(result.fields[0]).toMatchObject({
      sourceField: 'item_code',
      suggestedTarget: 'sku_id',
      alternatives: ['sku_variant'],
      confidence: 0.96,
      reasoning: 'matches SKU',
      detectedFormat: null,
      sampleValues: ['SKU-1'],
    })
    // a null suggested_target carries through (so the mapping gate can require the operator to
    // map or ignore it); alternatives default to []
    expect(result.fields[1]).toMatchObject({
      sourceField: 'note',
      suggestedTarget: null,
      alternatives: [],
    })
  })
})

describe('createCsvTemplate (REAL: Slice-16a columns[] contract, D89)', () => {
  const INPUT = {
    sourceId: 'weekly_export',
    templateName: 'Weekly export',
    templateType: 'sales',
    columns: [
      { src_key: 'item_code', dest_key: 'sku_id' },
      {
        src_key: 'amount',
        dest_key: 'unit_sale_price',
        src_decimal_separator: ',' as const,
        src_thousand_separator: '.' as const, // EU dot-thousands (422s until 16b; sent anyway)
        src_is_percentage: false,
      },
      { src_key: 'junk', dest_key: '__ignore__' },
    ],
  }

  it('fixture mode mirrors the real 16c create (create-as-ACTIVE: active v1, no draft)', async () => {
    const created = await createCsvTemplate(INPUT)
    expect(created).toMatchObject({
      templateName: 'Weekly export',
      templateType: 'sales',
      activeVersion: 1,
      draftVersion: null,
    })
    expect(created.templateId.length).toBeGreaterThan(0)
  })

  describe('real mode', () => {
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

    it('POSTs the semantic columns[] body (source_id/template_type/columns, NO mapping_rules)', async () => {
      const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
        async () =>
          ({
            ok: true,
            status: 201,
            json: async () => ({
              template_id: 'tmpl_real_1',
              template_name: 'Weekly export',
              template_type: 'sales',
              active_version: 1,
              draft_version: null,
            }),
          }) as unknown as Response,
      )
      vi.stubGlobal('fetch', fetchMock)

      const created = await createCsvTemplate(INPUT)
      const [url, init] = fetchMock.mock.calls[0]
      expect(url).toBe('http://test.local/api/v1/mapping-templates')
      const body = JSON.parse((init as RequestInit).body as string)
      expect(body).toEqual({
        source_id: 'weekly_export',
        template_name: 'Weekly export',
        template_type: 'sales',
        columns: INPUT.columns,
      })
      expect('mapping_rules' in body).toBe(false) // extra-forbidden -> would 422
      // ignored column carried as __ignore__; format declarations preserved (incl EU "." thousands)
      expect(body.columns[2]).toEqual({ src_key: 'junk', dest_key: '__ignore__' })
      expect(body.columns[1].src_thousand_separator).toBe('.')
      // response read gracefully: 16c create-as-ACTIVE returns active v1, no draft
      expect(created).toMatchObject({
        templateId: 'tmpl_real_1',
        activeVersion: 1,
        draftVersion: null,
      })
    })
  })
})
