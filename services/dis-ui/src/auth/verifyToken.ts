import { errors, jwtVerify } from 'jose'

import type { AuthSnapshot } from './AuthSnapshot'
import { snapshotFromClaims, TokenInvalidError } from './AuthSnapshot'
import { STUB_AUDIENCE, STUB_ISSUER, STUB_SECRET } from './dev/devStubSecret'

// DEV-STUB ONLY token verifier. Auth0 mode never calls this: the @auth0/auth0-react
// SDK verifies the ID token and exposes its claims as `user`, and the access token
// is verified server-side by dis-ui-server. This path exists only for the local
// dev-stub flow (VITE_AUTH_MODE=dev-stub, dev build only) where there is no backend
// and the stub JWT is HMAC-signed client-side. The claim->snapshot mapping is the
// shared snapshotFromClaims (namespaced https://sevyn8.com/* claims), identical to
// the Auth0 path, so both modes produce the same AuthSnapshot shape.

// Re-exported for callers/tests that imported it from here historically; it now
// lives in AuthSnapshot.ts (shared by the dev-stub and Auth0 derivations).
export { TokenInvalidError } from './AuthSnapshot'
export type { TokenInvalidReason } from './AuthSnapshot'

const KEY = new TextEncoder().encode(STUB_SECRET)

// Verifies a raw dev-stub token and returns the decoded AuthSnapshot. Throws
// TokenInvalidError on expiry, signature/format failure, or invalid claims so the
// caller (the dev-stub AuthProvider) can clear the token and leave the user
// unauthenticated.
export async function verifyToken(raw: string): Promise<AuthSnapshot> {
  try {
    const { payload } = await jwtVerify(raw, KEY, { issuer: STUB_ISSUER, audience: STUB_AUDIENCE })
    return snapshotFromClaims(payload as Record<string, unknown>)
  } catch (err) {
    if (err instanceof TokenInvalidError) {
      throw err
    }
    if (err instanceof errors.JWTExpired) {
      throw new TokenInvalidError('expired', 'Token has expired')
    }
    throw new TokenInvalidError('malformed', 'Token failed signature or format verification')
  }
}
