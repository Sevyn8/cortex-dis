import { SignJWT } from 'jose'

import type { StubPersona } from './personas'
import { STUB_AUDIENCE, STUB_EXPIRY, STUB_ISSUER, STUB_SECRET } from './devStubSecret'

const KEY = new TextEncoder().encode(STUB_SECRET)

// DEV ONLY. Mints a local HMAC-signed stub JWT for a persona, carrying the
// Customer Master claim set Sanjeev's slice-2 fake pins (sub via setSubject, plus
// tenant_id / store_id / roles). No profile claims (email/name) - those are not
// token claims. It must never run in a production bundle: minting tokens
// client-side is a dev affordance, and there is no Customer Master here. The
// verify path (verifyToken.ts) is the seam that later swaps HMAC for JWKS.
export async function signStubToken(persona: StubPersona): Promise<string> {
  if (import.meta.env.PROD) {
    throw new Error('signStubToken is dev-only and must not run in a production build')
  }

  // Namespaced Customer Master claims (https://sevyn8.com/*), matching the Auth0
  // ID-token shape the SDK exposes and the dis-ui-server verifier reads. `sub`
  // stays the standard subject; the principal id is the namespaced user_id.
  return new SignJWT({
    'https://sevyn8.com/user_id': persona.sub,
    'https://sevyn8.com/tenant_id': persona.tenant_id,
    'https://sevyn8.com/store_id': persona.store_id,
    'https://sevyn8.com/roles': persona.roles,
  })
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(persona.sub)
    .setIssuedAt()
    .setIssuer(STUB_ISSUER)
    .setAudience(STUB_AUDIENCE)
    .setExpirationTime(STUB_EXPIRY)
    .sign(KEY)
}
