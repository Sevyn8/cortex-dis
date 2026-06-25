import type { AuthSnapshot } from './AuthSnapshot'
import { isSuperAdmin } from './AuthSnapshot'

function snapshot(roles: string[]): AuthSnapshot {
  return { userId: 'u_x', tenantId: null, storeId: null, roles }
}

describe('isSuperAdmin', () => {
  it('is true when roles include atlas:schema:publish', () => {
    expect(isSuperAdmin(snapshot(['atlas:schema:publish', 'dis:read']))).toBe(true)
  })

  it('is false for ops roles (ops is not super admin)', () => {
    expect(isSuperAdmin(snapshot(['dis:ops', 'dis:read', 'dis:mapping_admin']))).toBe(false)
  })

  it('is false for tenant roles', () => {
    expect(isSuperAdmin(snapshot(['dis:upload', 'dis:read']))).toBe(false)
  })

  it('is false for an empty roles list', () => {
    expect(isSuperAdmin(snapshot([]))).toBe(false)
  })
})
