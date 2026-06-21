import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import type { ReactNode } from 'react'

import { clearToken, writeToken } from '../../auth/storage'
import {
  CATALOG_FIXTURE,
  IGNORE_FIELD,
  canonicalTargetKeys,
  getTemplateMappingFields,
  getTemplateMappingFieldsForType,
  useTemplateMappingFields,
} from './mapping-fields'
import type { FieldDatatype, FieldSection } from './mapping-fields'

const SECTIONS: FieldSection[] = ['sale_event', 'change_event']
const DATATYPES: FieldDatatype[] = [
  'text',
  'integer',
  'number',
  'date',
  'datetime',
  'boolean',
  'choice',
  'json',
]

// T1: the catalog fixture must match the real TemplateMappingField contract
// (services/dis-ui-server/.../schemas/mapping_fields.py) so the real-mode switch is a swap.
describe('template-mapping-fields catalog (fixture shaped to the real contract)', () => {
  it('every entry has the contract fields with valid enums', () => {
    for (const field of CATALOG_FIXTURE) {
      expect(typeof field.key).toBe('string')
      expect(typeof field.display_name).toBe('string')
      expect(SECTIONS).toContain(field.section)
      expect(typeof field.mandatory).toBe('boolean')
      expect(DATATYPES).toContain(field.datatype)
      expect(typeof field.description).toBe('string')
      // allowed_values present only for choice fields
      if (field.datatype === 'choice') {
        expect(Array.isArray(field.allowed_values)).toBe(true)
        expect(field.allowed_values?.length).toBeGreaterThan(0)
      }
      // max_length, when present, is a positive number
      if (field.max_length !== undefined) {
        expect(field.max_length).toBeGreaterThan(0)
      }
    }
  })

  it('carries the real event field set (both sections), not the old curated subset', () => {
    const sale = CATALOG_FIXTURE.filter((f) => f.section === 'sale_event').map((f) => f.key)
    const change = CATALOG_FIXTURE.filter((f) => f.section === 'change_event').map((f) => f.key)
    // real sale_event keys
    expect(sale).toEqual(
      expect.arrayContaining(['sku_id', 'quantity', 'source_sale_timestamp', 'currency']),
    )
    // real change_event keys
    expect(change).toEqual(
      expect.arrayContaining(['event_category', 'attribute_name', 'source_event_timestamp']),
    )
    // the catalog is event-only: legacy hot/identity targets are absent
    const allKeys = CATALOG_FIXTURE.map((f) => f.key)
    for (const absent of [
      'store_id',
      'current_retail_price',
      'product_name',
      'product_description',
      'tax_treatment',
    ]) {
      expect(allKeys).not.toContain(absent)
    }
  })

  it('exposes mandatory + section metadata (both sections present, some mandatory)', () => {
    expect(CATALOG_FIXTURE.some((f) => f.section === 'sale_event')).toBe(true)
    expect(CATALOG_FIXTURE.some((f) => f.section === 'change_event')).toBe(true)
    expect(CATALOG_FIXTURE.some((f) => f.mandatory)).toBe(true)
    // a known choice field carries its allowed values
    const subtype = CATALOG_FIXTURE.find(
      (f) => f.section === 'sale_event' && f.key === 'event_subtype',
    )
    expect(subtype?.allowed_values).toEqual(['SALE', 'RETURN', 'VOID'])
  })

  it('getter returns a copy of the catalog (fixture mode)', async () => {
    const result = await getTemplateMappingFields()
    expect(result).toHaveLength(CATALOG_FIXTURE.length)
    expect(result).not.toBe(CATALOG_FIXTURE)
  })

  it('canonicalTargetKeys de-dupes keys across sections', () => {
    const keys = canonicalTargetKeys(CATALOG_FIXTURE)
    expect(new Set(keys).size).toBe(keys.length) // no duplicates
    expect(keys).toContain('sku_id') // appears in both sections, listed once
  })
})

// T10: real-mode wiring (mocked fetch).
describe('template-mapping-fields real mode (T10)', () => {
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

  it('GETs /api/v1/template-mapping-fields with a Bearer and parses the catalog', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () =>
        ({ ok: true, status: 200, json: async () => CATALOG_FIXTURE }) as unknown as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    const fields = await getTemplateMappingFields()
    expect(fields).toEqual(CATALOG_FIXTURE)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/template-mapping-fields')
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok-123' })
  })
})

// Chunk 2: the WIRED type-aware catalog (?template_type=). Additive; the legacy no-param getter
// above is unchanged. Fixture-backed in default mode; the param is appended in real mode.
describe('type-aware template-mapping-fields (Chunk 2, fixture mode)', () => {
  it('returns the sales field set with the 10-key shape + the __ignore__ sentinel', async () => {
    const fields = await getTemplateMappingFieldsForType('sales')
    const keys = fields.map((f) => f.key)
    expect(keys).toEqual(expect.arrayContaining(['sku_id', 'quantity', 'source_sale_timestamp']))
    // the __ignore__ sentinel: section 'system', null datatype/sink (the Ignore representation)
    const ignore = fields.find((f) => f.key === '__ignore__')
    expect(ignore).toBeDefined()
    expect(ignore?.section).toBe('system')
    expect(ignore?.datatype).toBeNull()
    expect(ignore?.sink).toBeNull()
    // uniform 10-key shape: every entry carries sink + constraints keys (possibly null)
    for (const f of fields) {
      expect('sink' in f).toBe(true)
      expect('constraints' in f).toBe(true)
    }
  })

  it('returns the snapshot field set (current-position), distinct from sales', async () => {
    const snapshot = await getTemplateMappingFieldsForType('snapshot')
    const keys = snapshot.map((f) => f.key)
    expect(keys).toEqual(expect.arrayContaining(['current_retail_price', 'stock_on_hand']))
    expect(keys).toContain('__ignore__')
  })

  it('an unknown template type degrades to just the __ignore__ sentinel (no crash)', async () => {
    const fields = await getTemplateMappingFieldsForType('not_a_type')
    expect(fields).toEqual([IGNORE_FIELD])
  })
})

// The bare hook must NOT fire without a valid template_type: the type-required backend rejects a
// param-less GET (400). The unified connector route passes `enabled = (branch === 'pos')`.
describe('useTemplateMappingFields enabled gate', () => {
  function makeWrapper() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children)
  }

  it('does not fetch when disabled (no param-less call)', () => {
    const { result } = renderHook(() => useTemplateMappingFields(false), {
      wrapper: makeWrapper(),
    })
    expect(result.current.fetchStatus).toBe('idle')
    expect(result.current.data).toBeUndefined()
  })

  it('fetches when enabled (default)', async () => {
    const { result } = renderHook(() => useTemplateMappingFields(), { wrapper: makeWrapper() })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(result.current.data).toHaveLength(CATALOG_FIXTURE.length)
  })
})

describe('type-aware template-mapping-fields real mode (Chunk 2)', () => {
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

  it('GETs /api/v1/template-mapping-fields?template_type=sales with a Bearer', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () =>
        ({ ok: true, status: 200, json: async () => [IGNORE_FIELD] }) as unknown as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    await getTemplateMappingFieldsForType('sales')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/template-mapping-fields?template_type=sales')
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok-123' })
  })
})
