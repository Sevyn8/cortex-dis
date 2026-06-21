import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import {
  __resetMappingsFixture,
  getMappingVersion,
  getMappingVersions,
  getStagedVersion,
  promoteStagedVersion,
  rejectStagedVersion,
} from './mappings'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}
const otherTenant: AuthSnapshot = { ...tenant, tenantId: 't_other00001' }

describe('mapping fixtures (fixture mode)', () => {
  it('returns the source version list with exactly one active version', async () => {
    const versions = await getMappingVersions(tenant, 'manual_csv_upload')
    expect(versions.length).toBe(3)
    expect(versions.filter((v) => v.status === 'active')).toHaveLength(1)
    expect(versions.map((v) => v.status).sort()).toEqual(['active', 'deprecated', 'staged'])
  })

  it('returns an empty list for an unknown source', async () => {
    expect(await getMappingVersions(tenant, 'src_unknown')).toEqual([])
  })

  it('returns an empty list for another tenant (own-tenant only)', async () => {
    expect(await getMappingVersions(otherTenant, 'manual_csv_upload')).toEqual([])
  })

  it('returns the full definition for a known version', async () => {
    const detail = await getMappingVersion(tenant, 'manual_csv_upload', 2)
    expect(detail?.status).toBe('active')
    expect(detail?.mapping_rules.rename).toMatchObject({ item_code: 'sku_id' })
  })

  it('returns null for an unknown version', async () => {
    expect(await getMappingVersion(tenant, 'manual_csv_upload', 99)).toBeNull()
  })
})

describe('mapping version transitions (demand list 2.8/2.9, D22)', () => {
  beforeEach(() => {
    __resetMappingsFixture()
  })

  it('finds the staged version', () => {
    expect(getStagedVersion(tenant, 'manual_csv_upload')?.version).toBe(3)
    expect(getStagedVersion(tenant, 'src_unknown')).toBeNull()
  })

  it('promote moves staged to active and old active to deprecated', async () => {
    expect(promoteStagedVersion(tenant, 'manual_csv_upload')).toEqual({ promoted: 3, deprecated: 2 })
    const versions = await getMappingVersionsSorted()
    expect(versions[3]).toBe('active')
    expect(versions[2]).toBe('deprecated')
    // staged is gone after promote
    expect(getStagedVersion(tenant, 'manual_csv_upload')).toBeNull()
  })

  it('reject moves staged to deprecated and leaves active', async () => {
    expect(rejectStagedVersion(tenant, 'manual_csv_upload')).toEqual({ rejected: 3 })
    const versions = await getMappingVersionsSorted()
    expect(versions[3]).toBe('deprecated')
    expect(versions[2]).toBe('active')
  })

  it('throws when there is no staged version to promote or reject', () => {
    expect(() => promoteStagedVersion(tenant, 'src_unknown')).toThrow(/no staged/)
    expect(() => rejectStagedVersion(tenant, 'src_unknown')).toThrow(/no staged/)
  })
})

async function getMappingVersionsSorted(): Promise<Record<number, string>> {
  const versions = await getMappingVersions(tenant, 'manual_csv_upload')
  return Object.fromEntries(versions.map((v) => [v.version, v.status]))
}
