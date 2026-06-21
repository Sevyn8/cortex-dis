import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { AUDIT_HEALTHY_TRACE_ID, getAuditTrace } from './audit'
import { QUARANTINE_TRACE_IDS } from './quarantine'
import { OPS_AUDIT_TRACE_IDS } from './ops-cross-tenant'

const UNKNOWN_TRACE_ID = '0190ac0e-1a01-7001-8a01-0000000000ff'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}
const otherTenant: AuthSnapshot = { ...tenant, tenantId: 't_other00001' }

describe('audit fixtures (fixture mode)', () => {
  it('returns the healthy trace lifecycle with mapping_version_id on the mapped stage', async () => {
    const trace = await getAuditTrace(tenant, AUDIT_HEALTHY_TRACE_ID)
    expect(trace).not.toBeNull()
    expect(trace?.stages.map((s) => s.stage)).toEqual(['received', 'validated', 'mapped', 'committed'])
    const mapped = trace?.stages.find((s) => s.stage === 'mapped')
    expect(mapped?.mapping_version_id).toBe(1)
  })

  it('returns a quarantined terminal stage with an error_code', async () => {
    const trace = await getAuditTrace(tenant, QUARANTINE_TRACE_IDS.acmeCanonical)
    const terminal = trace?.stages.at(-1)
    expect(terminal?.stage).toBe('quarantined')
    expect(terminal?.error_code).toBe('CANONICAL_SHAPE_INVALID')
  })

  it('returns null for an unknown trace_id', async () => {
    expect(await getAuditTrace(tenant, UNKNOWN_TRACE_ID)).toBeNull()
  })

  it('returns null for a trace owned by another tenant (own-tenant only)', async () => {
    expect(await getAuditTrace(otherTenant, AUDIT_HEALTHY_TRACE_ID)).toBeNull()
  })

  // T9 AUTHORIZATION BOUNDARY: a tenant cannot look up a trace that belongs to another tenant.
  // The Beta fleet trace is reachable only via the ops cross-tenant lookup (isOps); the
  // tenant-scoped getter returns null for it.
  it('AUTHORIZATION BOUNDARY: a tenant cannot look up another tenant trace', async () => {
    expect(await getAuditTrace(tenant, OPS_AUDIT_TRACE_IDS.betaHealthy)).toBeNull()
  })
})
