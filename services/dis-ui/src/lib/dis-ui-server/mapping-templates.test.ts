import { afterEach, beforeEach, vi } from 'vitest'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { clearToken, writeToken } from '../../auth/storage'
import { DisUiServerHttpError } from './client'
import {
  activeTemplateVersion,
  getMappingTemplate,
  getMappingTemplates,
  patchMappingTemplate,
} from './mapping-templates'
import type { SourceMappingRules } from './mapping-templates'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}
const STATUSES = ['draft', 'staged', 'active', 'deprecated']

// T2: fixtures shaped to the real 14b mapping_templates contract.
describe('mapping-templates fixtures (shaped to the real contract)', () => {
  it('list returns lineage summaries with the contract fields', async () => {
    const list = await getMappingTemplates(tenant)
    expect(list.length).toBeGreaterThan(0)
    for (const t of list) {
      for (const key of [
        'template_id',
        'source_id',
        'template_name',
        'ingestion_mode',
        'latest_version',
        'active_version',
        'staged_version',
        'draft_version',
        'versions_count',
        'created_at',
        'latest_version_created_at',
      ]) {
        expect(t).toHaveProperty(key)
      }
      // summary carries NO versions[] (that is detail-only)
      expect((t as Record<string, unknown>).versions).toBeUndefined()
    }
  })

  it('list filters by source_id', async () => {
    const all = await getMappingTemplates(tenant)
    const scoped = await getMappingTemplates(tenant, 'manual_csv_upload')
    expect(scoped.every((t) => t.source_id === 'manual_csv_upload')).toBe(true)
    expect(scoped.length).toBeGreaterThan(0)
    const none = await getMappingTemplates(tenant, 'no_such_source')
    expect(none).toEqual([])
    expect(all.length).toBeGreaterThanOrEqual(scoped.length)
  })

  it('detail returns the version lineage with raw-D49 mapping_rules', async () => {
    const list = await getMappingTemplates(tenant, 'manual_csv_upload')
    const sales = list.find((t) => t.template_name === 'Sales')
    expect(sales).toBeDefined()
    const detail = await getMappingTemplate(tenant, sales!.template_id)
    expect(detail.versions.length).toBe(detail.versions_count)
    for (const v of detail.versions) {
      expect(STATUSES).toContain(v.status)
      for (const key of [
        'mapping_version_id',
        'version',
        'status',
        'mapping_rules',
        'field_count',
        'transform_count',
        'predecessor_version_id',
        'created_at',
        'created_by_user_id',
        'activated_at',
        'deprecated_at',
      ]) {
        expect(v).toHaveProperty(key)
      }
      // raw D49 mapping_rules: the two concerns are present
      expect(v.mapping_rules).toHaveProperty('rename')
      expect(v.mapping_rules).toHaveProperty('normalize')
      expect(v.mapping_rules).toHaveProperty('cast')
      expect(v.mapping_rules).toHaveProperty('derive')
    }
    // the active version carries the REAL normalize shape: {op, args} with a format + a
    // decimal_separator (FM3 - the real arg names, not the flat T2 draft)
    const active = activeTemplateVersion(detail)
    expect(active?.status).toBe('active')
    const normalizeJson = JSON.stringify(active?.mapping_rules.normalize)
    expect(normalizeJson).toMatch(/"op":"parse_datetime"/)
    expect(normalizeJson).toMatch(/"args":\{"format":/)
    expect(normalizeJson).toMatch(/decimal_separator/)
    // store_id is never a field-mapping target (FM3)
    expect(Object.values(active?.mapping_rules.rename ?? {})).not.toContain('store_id')
  })

  it('detail throws for an unknown template (404 contract)', async () => {
    await expect(getMappingTemplate(tenant, 'no-such-template')).rejects.toThrow(/not found/)
  })
})

// T10: real-mode wiring (mocked fetch). The real wire OMITS ingestion_mode; the branch
// defaults it to 'file'.
describe('mapping-templates real mode (T10)', () => {
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

  const RULES: SourceMappingRules = {
    version: 1,
    rename: { item_code: 'sku_id' },
    normalize: {},
    cast: {},
    derive: {},
  }
  // A raw detail as the real backend serves it: NO ingestion_mode field.
  const RAW_DETAIL = {
    template_id: '0190ac10-5a00-7000-8a00-0000000000a1',
    source_id: 'manual_csv_upload',
    template_name: 'Sales',
    latest_version: 1,
    active_version: null,
    staged_version: null,
    draft_version: 1,
    versions_count: 1,
    created_at: '2026-06-06T00:00:00Z',
    latest_version_created_at: '2026-06-06T00:00:00Z',
    versions: [
      {
        mapping_version_id: 1,
        version: 1,
        status: 'draft',
        mapping_rules: RULES,
        field_count: 1,
        transform_count: 0,
        predecessor_version_id: null,
        created_at: '2026-06-06T00:00:00Z',
        created_by_user_id: null,
        activated_at: null,
        deprecated_at: null,
      },
    ],
  }
  // The list endpoint serves the summary (no versions[]).
  const RAW_SUMMARY: Record<string, unknown> = { ...RAW_DETAIL }
  delete RAW_SUMMARY.versions

  function ok(body: unknown, status = 200): Response {
    return { ok: true, status, json: async () => body } as unknown as Response
  }

  it('list GETs /api/v1/mapping-templates and defaults ingestion_mode to file', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      ok([RAW_SUMMARY]),
    )
    vi.stubGlobal('fetch', fetchMock)
    const list = await getMappingTemplates(tenant)
    expect(fetchMock.mock.calls[0][0]).toBe('http://test.local/api/v1/mapping-templates')
    expect(list[0].template_name).toBe('Sales')
    expect(list[0].ingestion_mode).toBe('file') // contract diff: defaulted when absent
  })

  it('list appends ?source_id= when filtering by source', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      ok([RAW_SUMMARY]),
    )
    vi.stubGlobal('fetch', fetchMock)
    await getMappingTemplates(tenant, 'manual_csv_upload')
    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://test.local/api/v1/mapping-templates?source_id=manual_csv_upload',
    )
  })

  it('detail GETs /api/v1/mapping-templates/{id} and defaults ingestion_mode to file', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      ok(RAW_DETAIL),
    )
    vi.stubGlobal('fetch', fetchMock)
    const detail = await getMappingTemplate(tenant, '0190ac10-5a00-7000-8a00-0000000000a1')
    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://test.local/api/v1/mapping-templates/0190ac10-5a00-7000-8a00-0000000000a1',
    )
    expect(detail.ingestion_mode).toBe('file')
    expect(detail.versions[0].status).toBe('draft')
  })

  it('detail maps a 404 to DisUiServerHttpError (throw-style)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          ({
            ok: false,
            status: 404,
            json: async () => ({ error: { code: 'resource_not_found' } }),
          }) as unknown as Response,
      ),
    )
    await expect(getMappingTemplate(tenant, 'nope')).rejects.toBeInstanceOf(DisUiServerHttpError)
  })

  it('patch PATCHes MappingTemplatePatch as a JSON body', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      ok(RAW_DETAIL),
    )
    vi.stubGlobal('fetch', fetchMock)
    const body = { template_name: 'Renamed' }
    await patchMappingTemplate('0190ac10-5a00-7000-8a00-0000000000a1', body)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(
      'http://test.local/api/v1/mapping-templates/0190ac10-5a00-7000-8a00-0000000000a1',
    )
    expect((init as RequestInit).method).toBe('PATCH')
    expect((init as RequestInit).body).toBe(JSON.stringify(body))
  })
})

// Create-as-ACTIVE (D88): create in FIXTURE mode synthesizes a LIVE v1 (active_version=1); edit
// (patch) still synthesizes a DRAFT (the D17 lifecycle for changes). No mutable store, no backend.
describe('mapping-templates create/patch fixture mode', () => {
  it('patchMappingTemplate synthesizes a DRAFT echoing the patch', async () => {
    const detail = await patchMappingTemplate('tmpl-1', { template_name: 'Renamed' })
    expect(detail.template_name).toBe('Renamed')
    expect(detail.versions[0].status).toBe('draft')
  })
})
