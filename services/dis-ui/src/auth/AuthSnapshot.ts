// The in-memory identity + authz for the signed-in user, derived from the auth
// token's NAMESPACED Customer Master claims (https://sevyn8.com/*), consistent
// with the dis-ui-server verifier. In Auth0 mode the claims come from the SDK's
// verified `user` (the ID token); in dev-stub mode from verifyToken.ts. It
// carries NO profile fields (email, name, tenant_name) - those come from the
// separate dis-ui-server GET /me profile call (see lib/dis-ui-server/types.ts).

export type AuthSnapshot = {
  userId: string
  // null for ops users, who are cross-tenant; a concrete tenant for tenant users.
  tenantId: string | null
  storeId: string | null
  // Role strings from the token, e.g. dis:upload / dis:read / dis:ops /
  // dis:mapping_admin. Customer Master issues no roles claim yet, so this is read
  // if present else empty (deny-by-default); DB-side roles is step 2.
  roles: string[]
}

// The Customer Master Auth0 custom-claim namespace, matching the dis-ui-server
// verifier (services/dis-ui-server auth/verifier.py). `sub` stays the standard
// Auth0 subject; the principal id is the namespaced user_id (the CM internal
// UUID the backend keys on).
const CLAIMS_NAMESPACE = 'https://sevyn8.com/'
const CLAIM_USER_ID = `${CLAIMS_NAMESPACE}user_id`
const CLAIM_TENANT_ID = `${CLAIMS_NAMESPACE}tenant_id`
const CLAIM_STORE_ID = `${CLAIMS_NAMESPACE}store_id`
const CLAIM_ROLES = `${CLAIMS_NAMESPACE}roles`

export type TokenInvalidReason = 'expired' | 'malformed' | 'invalid-claims'

// Thrown when a token's claims cannot produce a valid snapshot. Carries a coarse
// reason and never the raw token/claim values. Lives here (not verifyToken.ts) so
// both the dev-stub verifier and the Auth0 claim derivation share it with no cycle.
export class TokenInvalidError extends Error {
  readonly reason: TokenInvalidReason

  constructor(reason: TokenInvalidReason, message: string) {
    super(message)
    this.name = 'TokenInvalidError'
    this.reason = reason
  }
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

// Maps a verified claim set (Auth0 `user` or the dev-stub payload) to the
// AuthSnapshot, reading the namespaced Customer Master claims. The principal id
// is the namespaced user_id (NOT `sub`, which is the auth0|... subject). A
// missing/misshapen required claim throws TokenInvalidError('invalid-claims') so
// the caller leaves the user unauthenticated (fail-safe). `user_type` is present
// in the claims but intentionally not surfaced: no UI gate uses it (that is step 2).
export function snapshotFromClaims(claims: Record<string, unknown>): AuthSnapshot {
  const userId = claims[CLAIM_USER_ID]
  if (typeof userId !== 'string' || userId.length === 0) {
    throw new TokenInvalidError('invalid-claims', 'Token is missing the user_id claim')
  }

  const rawTenant = claims[CLAIM_TENANT_ID]
  const tenantId = rawTenant === null || rawTenant === undefined ? null : rawTenant
  if (tenantId !== null && typeof tenantId !== 'string') {
    throw new TokenInvalidError('invalid-claims', 'Token has an invalid tenant_id claim')
  }

  const rawStore = claims[CLAIM_STORE_ID]
  const storeId = rawStore === null || rawStore === undefined ? null : rawStore
  if (storeId !== null && typeof storeId !== 'string') {
    throw new TokenInvalidError('invalid-claims', 'Token has an invalid store_id claim')
  }

  const rawRoles = claims[CLAIM_ROLES]
  const roles = rawRoles === undefined ? [] : rawRoles
  if (!isStringArray(roles)) {
    throw new TokenInvalidError('invalid-claims', 'Token has an invalid roles claim')
  }

  return { userId, tenantId, storeId, roles }
}

// The only tenant-vs-ops gate for Phase 1. Ops surfaces require this; everything
// else is tenant-default. No fine-grained permission gating exists yet (D25 open).
export function isOps(snapshot: AuthSnapshot): boolean {
  return snapshot.roles.includes('dis:ops')
}

// The Atlas console gate (A4). The Atlas console (schema authoring/ratify/publish) is
// platform-scoped and Super-Admin-only; this mirrors isOps. The role string matches the
// dis-ui-server require_super_admin gate (auth/scope.py SUPER_ADMIN_ROLE). The REAL role
// is Customer Master issued at global scope and lands in A5 (Sanjeev's swimlane); this is
// the same role string the BFF stub checks, so the UI gate and the server gate agree.
export function isSuperAdmin(snapshot: AuthSnapshot): boolean {
  return snapshot.roles.includes('atlas:schema:publish')
}
