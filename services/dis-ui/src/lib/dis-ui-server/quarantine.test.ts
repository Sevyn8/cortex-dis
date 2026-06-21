import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import {
  CHAIN_DEPTH_CAP,
  QUARANTINE_TRACE_IDS,
  __resetQuarantineFixture,
  getQuarantine,
  getQuarantineRow,
  postResubmit,
} from './quarantine'
import { OPS_QUARANTINE_TRACE_IDS } from './ops-cross-tenant'

const UNKNOWN_TRACE_ID = '0190ac0e-1a01-7001-8a01-0000000000ff'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}
const otherTenant: AuthSnapshot = { ...tenant, tenantId: 't_nofixtures01' }

describe('quarantine fixtures (fixture mode)', () => {
  it('returns the tenant rows', async () => {
    const rows = await getQuarantine(tenant)
    expect(rows.length).toBeGreaterThan(0)
    expect(rows.map((r) => r.trace_id)).toContain(QUARANTINE_TRACE_IDS.acmeCanonical)
  })

  it('returns an empty list for a tenant with no rows', async () => {
    expect(await getQuarantine(otherTenant)).toEqual([])
  })

  it('returns row detail with payload and mapping version', async () => {
    const detail = await getQuarantineRow(QUARANTINE_TRACE_IDS.acmeCanonical)
    expect(detail.mapping_version).toBe(1)
    expect(detail.original_payload).toMatchObject({ sku: 'A123' })
  })

  it('rejects for an unknown trace_id', async () => {
    await expect(getQuarantineRow(UNKNOWN_TRACE_ID)).rejects.toThrow(/no fixture/)
  })

  // T9 AUTHORIZATION BOUNDARY: the tenant-scoped getter returns ONLY the caller's rows and
  // never another tenant's. The fleet fixture (ops-cross-tenant) carries Beta/Delta rows,
  // but a tenant caller must never see them; fleet scope is requested only when isOps.
  it('AUTHORIZATION BOUNDARY: the tenant getter never returns another tenant rows', async () => {
    const acmeTraceIds = (await getQuarantine(tenant)).map((r) => r.trace_id)
    expect(acmeTraceIds.length).toBeGreaterThan(0)
    expect(acmeTraceIds).not.toContain(OPS_QUARANTINE_TRACE_IDS.betaCanonical)
    expect(acmeTraceIds).not.toContain(OPS_QUARANTINE_TRACE_IDS.deltaFk)
    // a different tenant receives none of Acme's rows
    expect(await getQuarantine(otherTenant)).toEqual([])
  })
})

describe('quarantine resubmit fixture (demand list 4.3)', () => {
  beforeEach(() => {
    __resetQuarantineFixture()
  })

  it('records a replay resubmit: child trace, depth 1, accepted', async () => {
    const res = await postResubmit({
      resubmit_type: 'replay',
      parent_trace_id: QUARANTINE_TRACE_IDS.acmeCanonical,
    })
    expect(res.parent_trace_id).toBe(QUARANTINE_TRACE_IDS.acmeCanonical)
    expect(res.resubmit_type).toBe('replay')
    expect(res.chain_depth).toBe(1)
    expect(res.status).toBe('accepted')
    expect(res.trace_id).not.toBe(QUARANTINE_TRACE_IDS.acmeCanonical)
  })

  it('overlays the new state on the row detail after a resubmit', async () => {
    await postResubmit({ resubmit_type: 'replay', parent_trace_id: QUARANTINE_TRACE_IDS.acmeCanonical })
    const detail = await getQuarantineRow(QUARANTINE_TRACE_IDS.acmeCanonical)
    expect(detail.chain_depth).toBe(1)
    expect(detail.resubmits).toHaveLength(1)
    expect(detail.resubmits[0].chain_depth).toBe(1)
  })

  it('carries fixed_file through to the response', async () => {
    const res = await postResubmit({
      resubmit_type: 'fixed_file',
      parent_trace_id: QUARANTINE_TRACE_IDS.acmeCanonical,
    })
    expect(res.resubmit_type).toBe('fixed_file')
  })

  it('enforces the chain-depth cap: a fourth resubmit is rejected', async () => {
    for (let i = 0; i < CHAIN_DEPTH_CAP; i += 1) {
      await postResubmit({ resubmit_type: 'replay', parent_trace_id: QUARANTINE_TRACE_IDS.acmeCanonical })
    }
    const atCap = await getQuarantineRow(QUARANTINE_TRACE_IDS.acmeCanonical)
    expect(atCap.chain_depth).toBe(CHAIN_DEPTH_CAP)
    await expect(
      postResubmit({ resubmit_type: 'replay', parent_trace_id: QUARANTINE_TRACE_IDS.acmeCanonical }),
    ).rejects.toThrow(/cap/)
  })

  it('reports a row seeded at the cap as chain depth 3', async () => {
    const detail = await getQuarantineRow(QUARANTINE_TRACE_IDS.acmeNormalization)
    expect(detail.chain_depth).toBe(CHAIN_DEPTH_CAP)
  })
})
