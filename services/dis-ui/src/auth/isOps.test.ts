import type { AuthSnapshot } from './AuthSnapshot'
import { isOps } from './AuthSnapshot'

function snapshot(roles: string[]): AuthSnapshot {
  return { userId: 'u_x', tenantId: null, storeId: null, roles }
}

describe('isOps', () => {
  it('is true when roles include dis:ops', () => {
    expect(isOps(snapshot(['dis:ops', 'dis:read', 'dis:mapping_admin']))).toBe(true)
  })

  it('is false for tenant roles', () => {
    expect(isOps(snapshot(['dis:upload', 'dis:read']))).toBe(false)
  })

  it('is false for an empty roles list', () => {
    expect(isOps(snapshot([]))).toBe(false)
  })
})
