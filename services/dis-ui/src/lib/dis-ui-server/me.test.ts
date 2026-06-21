import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { PERSONAS } from '../../auth/dev/personas'
import type { StubPersona } from '../../auth/dev/personas'
import { ME_FIXTURES } from './fixtures'
import { getMe } from './me'

function toSnapshot(persona: StubPersona): AuthSnapshot {
  return {
    userId: persona.sub,
    tenantId: persona.tenant_id,
    storeId: persona.store_id,
    roles: persona.roles,
  }
}

const tenant = PERSONAS.find((p) => p.id === 'tenant')!
const ops = PERSONAS.find((p) => p.id === 'ops')!

describe('getMe (fixture mode)', () => {
  it('returns the tenant profile with tenant_name', async () => {
    const me = await getMe(toSnapshot(tenant))
    expect(me).toEqual({
      user_id: 'u_acmeuser0001',
      email: 'acme.user@example.test',
      name: 'Acme User',
      tenant_id: 't_acme9k2l1mn4',
      tenant_name: 'Acme Retail',
    })
  })

  it('returns the ops profile with null tenant_id and tenant_name', async () => {
    const me = await getMe(toSnapshot(ops))
    expect(me.tenant_id).toBeNull()
    expect(me.tenant_name).toBeNull()
    expect(me.user_id).toBe('u_opsdev0001')
    expect(me.email).toBe(ME_FIXTURES['u_opsdev0001'].email)
  })

  it('rejects for a user with no fixture', async () => {
    const unknown: AuthSnapshot = { ...toSnapshot(tenant), userId: 'no-such-user' }
    await expect(getMe(unknown)).rejects.toThrow(/no fixture/)
  })
})

describe('every /dev/login persona has a profile fixture', () => {
  it.each(PERSONAS.map((p) => [p.id, p] as const))('persona %s', (_id, persona) => {
    const fixture = ME_FIXTURES[persona.sub]
    expect(fixture).toBeDefined()
    expect(fixture.user_id).toBe(persona.sub)
    // The profile tenant_id aligns with the token persona's tenant_id.
    expect(fixture.tenant_id).toBe(persona.tenant_id)
  })
})
