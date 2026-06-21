import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { __resetMappingsFixture, getMappingVersions } from './mappings'
import { getShadowDiff, getShadowStats, promoteShadow, rejectShadow } from './shadow'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}
const SOURCE = 'manual_csv_upload'

describe('shadow rollout fixtures (demand list 2.6-2.9)', () => {
  beforeEach(() => {
    __resetMappingsFixture()
  })

  it('returns stats for a source with a staged version (2.6)', async () => {
    const stats = await getShadowStats(tenant, SOURCE)
    expect(stats).not.toBeNull()
    expect(stats?.staged_version).toBe(3)
    expect(stats?.active_version).toBe(2)
    expect(stats?.diff_differing).toBeGreaterThan(0)
    expect(stats?.diff_column).toBe('source_sale_timestamp')
  })

  it('returns diff sample rows using the canonical column (2.7)', async () => {
    const rows = await getShadowDiff(tenant, SOURCE)
    expect(rows.length).toBeGreaterThan(0)
    expect(rows.every((r) => r.column === 'source_sale_timestamp')).toBe(true)
  })

  it('returns null stats and empty diff for a source with no staged version', async () => {
    expect(await getShadowStats(tenant, 'src_unknown')).toBeNull()
    expect(await getShadowDiff(tenant, 'src_unknown')).toEqual([])
  })

  it('promote transitions staged to active and old active to deprecated (2.8)', async () => {
    const result = await promoteShadow(tenant, SOURCE)
    expect(result).toEqual({
      source_id: SOURCE,
      promoted_version: 3,
      deprecated_version: 2,
      status: 'promoted',
    })
    const versions = await getMappingVersions(tenant, SOURCE)
    const byVersion = Object.fromEntries(versions.map((v) => [v.version, v.status]))
    expect(byVersion[3]).toBe('active')
    expect(byVersion[2]).toBe('deprecated')
    expect(versions.filter((v) => v.status === 'active')).toHaveLength(1)
    // no staged version remains, so shadow stats are now null
    expect(await getShadowStats(tenant, SOURCE)).toBeNull()
  })

  it('reject transitions staged to deprecated and leaves active untouched (2.9)', async () => {
    const result = await rejectShadow(tenant, SOURCE)
    expect(result).toEqual({ source_id: SOURCE, rejected_version: 3, status: 'rejected' })
    const versions = await getMappingVersions(tenant, SOURCE)
    const byVersion = Object.fromEntries(versions.map((v) => [v.version, v.status]))
    expect(byVersion[3]).toBe('deprecated')
    expect(byVersion[2]).toBe('active')
  })
})
