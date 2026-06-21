import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import {
  __resetSourcesFixture,
  createSource,
  deprecateSource,
  deriveSourceId,
  getSources,
  makeSourceDraft,
  updateSource,
} from './sources'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

describe('sources CRUD fixture (fixture mode)', () => {
  beforeEach(() => {
    __resetSourcesFixture()
  })

  it('create adds a source and the list reflects it', async () => {
    const before = await getSources(tenant)
    await createSource(tenant, makeSourceDraft({ source_id: 'square_pos', name: 'Square POS', type: 'API', store: 'Acme Online' }))
    const after = await getSources(tenant)
    expect(after.length).toBe(before.length + 1)
    const created = after.find((s) => s.source_id === 'square_pos')
    expect(created?.status).toBe('active')
    expect(created?.name).toBe('Square POS')
  })

  it('edit updates display metadata but never the source_id', async () => {
    await updateSource(tenant, 'manual_csv_upload', { name: 'Renamed CSV', type: 'CSV', store: 'Acme HQ' })
    const sources = await getSources(tenant)
    const edited = sources.find((s) => s.source_id === 'manual_csv_upload')
    expect(edited?.name).toBe('Renamed CSV')
    expect(edited?.store).toBe('Acme HQ')
    // identity unchanged
    expect(sources.map((s) => s.source_id)).toContain('manual_csv_upload')
  })

  it('deprecate is a soft active-to-deprecated transition', async () => {
    await deprecateSource(tenant, 'manual_csv_upload')
    const sources = await getSources(tenant)
    expect(sources.find((s) => s.source_id === 'manual_csv_upload')?.status).toBe('deprecated')
    // still present (not deleted)
    expect(sources.some((s) => s.source_id === 'manual_csv_upload')).toBe(true)
  })

  it('the source-create draft shape matches the onboarding attach-to-new draft', () => {
    // CRUD create builds a SourceDraft from the explicit form fields.
    const crudDraft = makeSourceDraft({
      source_id: 'shopify_pos',
      name: 'Shopify POS',
      type: 'API',
      store: 'Acme Online',
    })
    // Onboarding attach-to-new builds one from its fields (name = label, type = kind,
    // source_id derived) via the SAME builder.
    const onboardingDraft = makeSourceDraft({
      source_id: deriveSourceId('Shopify POS'),
      name: 'Shopify POS',
      type: 'API',
      store: '',
    })
    expect(Object.keys(crudDraft).sort()).toEqual(Object.keys(onboardingDraft).sort())
    expect(Object.keys(crudDraft).sort()).toEqual(['name', 'source_id', 'store', 'type'])
  })
})
