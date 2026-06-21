import { QUARANTINE_TRACE_IDS } from './quarantine'
import {
  OPS_AUDIT_TRACE_IDS,
  __resetOpsCrossTenantFixture,
  getCrossTenantAuditTrace,
  getFleetQuarantine,
  postOpsResubmit,
} from './ops-cross-tenant'

describe('ops cross-tenant fixtures (fixture mode)', () => {
  beforeEach(() => {
    __resetOpsCrossTenantFixture()
  })

  it('returns a fleet-wide quarantine list spanning multiple tenants', async () => {
    const rows = await getFleetQuarantine()
    const tenantIds = new Set(rows.map((r) => r.tenant_id))
    expect(tenantIds.size).toBeGreaterThan(1)
    expect(tenantIds).toContain('t_acme9k2l1mn4')
    expect(rows.every((r) => r.tenant_name.length > 0)).toBe(true)
  })

  it('looks up a cross-tenant trace with its tenant, no tenant filter', async () => {
    const trace = await getCrossTenantAuditTrace(OPS_AUDIT_TRACE_IDS.betaHealthy)
    expect(trace).not.toBeNull()
    expect(trace?.tenant_id).toBe('t_beta7h2k9m3n')
    expect(trace?.tenant_name).toBe('Beta Stores')
  })

  it('ops resubmit carries tenant_id and respects the depth-3 cap', async () => {
    const res = await postOpsResubmit({
      resubmit_type: 'replay',
      parent_trace_id: QUARANTINE_TRACE_IDS.acmeCanonical,
      tenant_id: 't_acme9k2l1mn4',
    })
    expect(res.status).toBe('accepted')
    expect(res.chain_depth).toBe(1)

    // acmeNormalization is seeded at the cap -> rejected.
    await expect(
      postOpsResubmit({
        resubmit_type: 'replay',
        parent_trace_id: QUARANTINE_TRACE_IDS.acmeNormalization,
        tenant_id: 't_acme9k2l1mn4',
      }),
    ).rejects.toThrow(/cap/)
  })
})
