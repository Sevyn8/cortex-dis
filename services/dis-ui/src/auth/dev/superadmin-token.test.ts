import { describe, expect, it } from 'vitest'

import { isSuperAdmin } from '../AuthSnapshot'
import type { AuthSnapshot } from '../AuthSnapshot'
import { verifyToken } from '../verifyToken'
import { PERSONAS } from './personas'
import type { StubPersona } from './personas'
import { signStubToken } from './signStubToken'

// Permanent regression guard (A4): the super-admin stub-token CLAIMS ENVELOPE is correct,
// independent of the (interim, deferred) DevLogin token-delivery mechanism. This is the one
// super-admin auth fact testable pre-Auth0: that signStubToken -> verifyToken round-trips the
// persona's claims into an AuthSnapshot, and that isSuperAdmin reads the role correctly. When
// Auth0 replaces the stub-token mechanism (decisions.md D25, the verifyToken.ts HMAC->JWKS
// seam), the claim-to-snapshot mapping asserted here is the part that stays the same.
//
// This deliberately does NOT touch DevLogin.tsx or the baked-token delivery path (deferred to
// Auth0); it locks only the claims mapping.

function personaById(id: string): StubPersona {
  const persona = PERSONAS.find((p) => p.id === id)
  if (persona === undefined) {
    throw new Error(`expected a "${id}" dev persona`)
  }
  return persona
}

async function roundTrip(persona: StubPersona): Promise<AuthSnapshot> {
  return verifyToken(await signStubToken(persona))
}

describe('super-admin stub-token claims mapping (pre-Auth0 guard)', () => {
  it('PERSONAS contains a super_admin persona', () => {
    const superAdmin = personaById('super_admin')
    expect(superAdmin.tenant_id).toBeNull()
    expect(superAdmin.store_id).toBeNull()
    expect(superAdmin.roles).toContain('atlas:schema:publish')
  })

  it('signStubToken -> verifyToken round-trips the super-admin claims into an AuthSnapshot', async () => {
    const snapshot = await roundTrip(personaById('super_admin'))
    expect(snapshot.userId).toBe('u_superadmin01')
    expect(snapshot.tenantId).toBeNull()
    expect(snapshot.storeId).toBeNull()
    expect(snapshot.roles).toContain('atlas:schema:publish')
  })

  it('isSuperAdmin is true for the round-tripped super-admin snapshot', async () => {
    const snapshot = await roundTrip(personaById('super_admin'))
    expect(isSuperAdmin(snapshot)).toBe(true)
  })

  it('isSuperAdmin is false for the round-tripped tenant and ops snapshots', async () => {
    const tenant = await roundTrip(personaById('tenant'))
    expect(isSuperAdmin(tenant)).toBe(false)
    // sanity: the tenant claims still round-trip (tenant-scoped, not super admin).
    expect(tenant.tenantId).not.toBeNull()

    const ops = await roundTrip(personaById('ops'))
    expect(isSuperAdmin(ops)).toBe(false)
    // ops is cross-tenant but carries dis:ops, not atlas:schema:publish.
    expect(ops.tenantId).toBeNull()
    expect(ops.roles).toContain('dis:ops')
    expect(ops.roles).not.toContain('atlas:schema:publish')
  })
})
