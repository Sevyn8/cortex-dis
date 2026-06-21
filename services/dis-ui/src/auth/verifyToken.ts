import { errors, jwtVerify } from 'jose'
import type { JWTPayload } from 'jose'

import type { AuthSnapshot } from './AuthSnapshot'
import { STUB_AUDIENCE, STUB_ISSUER, STUB_SECRET } from './dev/devStubSecret'

export type TokenInvalidReason = 'expired' | 'malformed' | 'invalid-claims'

export class TokenInvalidError extends Error {
  readonly reason: TokenInvalidReason

  constructor(reason: TokenInvalidReason, message: string) {
    super(message)
    this.name = 'TokenInvalidError'
    this.reason = reason
  }
}

// HMAC key for the dev stub. Real-mode seam (decisions.md D25 / slice 13): replace
// this with a JWKS remote key set (jose createRemoteJWKSet) keyed by the Customer
// Master issuer/audience. The claim-to-snapshot mapping below stays the same.
const KEY = new TextEncoder().encode(STUB_SECRET)

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

// Maps the Customer Master token claims (sub, tenant_id, store_id, roles - the
// shape pinned by Sanjeev's slice-2 fakes, PROVISIONAL pending D25) to the
// AuthSnapshot. Profile fields (email, name, tenant_name) are NOT token claims and
// are not read here; they come from the GET /me profile call.
function toSnapshot(payload: JWTPayload): AuthSnapshot {
  const claims = payload as Record<string, unknown>
  const sub = payload.sub

  if (typeof sub !== 'string' || sub.length === 0) {
    throw new TokenInvalidError('invalid-claims', 'Token is missing a sub claim')
  }

  const tenantId =
    claims.tenant_id === null || claims.tenant_id === undefined ? null : claims.tenant_id
  if (tenantId !== null && typeof tenantId !== 'string') {
    throw new TokenInvalidError('invalid-claims', 'Token has an invalid tenant_id claim')
  }

  const storeId =
    claims.store_id === null || claims.store_id === undefined ? null : claims.store_id
  if (storeId !== null && typeof storeId !== 'string') {
    throw new TokenInvalidError('invalid-claims', 'Token has an invalid store_id claim')
  }

  const roles = claims.roles === undefined ? [] : claims.roles
  if (!isStringArray(roles)) {
    throw new TokenInvalidError('invalid-claims', 'Token has an invalid roles claim')
  }

  return { userId: sub, tenantId, storeId, roles }
}

// Verifies a raw token and returns the decoded AuthSnapshot. Throws
// TokenInvalidError on expiry, signature/format failure, or invalid claims so the
// caller (AuthContext) can clear the token and leave the user unauthenticated.
export async function verifyToken(raw: string): Promise<AuthSnapshot> {
  let payload: JWTPayload
  try {
    const result = await jwtVerify(raw, KEY, { issuer: STUB_ISSUER, audience: STUB_AUDIENCE })
    payload = result.payload
  } catch (err) {
    if (err instanceof errors.JWTExpired) {
      throw new TokenInvalidError('expired', 'Token has expired')
    }
    throw new TokenInvalidError('malformed', 'Token failed signature or format verification')
  }
  return toSnapshot(payload)
}
