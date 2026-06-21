// The in-memory identity + authz for the signed-in user, derived purely by
// decoding the auth token's claims (see verifyToken.ts). It mirrors the Customer
// Master token model that Sanjeev's slice-2 fakes pin (PROVISIONAL, pending D25):
// sub + tenant_id + store_id + a roles list. It carries NO profile fields (email,
// name, tenant_name) - those are not token claims; they come from the separate
// dis-ui-server GET /me profile call (see lib/dis-ui-server/types.ts MeResponse).

export type AuthSnapshot = {
  userId: string
  // null for ops users, who are cross-tenant; a concrete tenant for tenant users.
  tenantId: string | null
  storeId: string | null
  // Role strings from the token, e.g. dis:upload / dis:read / dis:ops /
  // dis:mapping_admin. Vocabulary is PROVISIONAL pending decisions.md D25.
  roles: string[]
}

// The only tenant-vs-ops gate for Phase 1. Ops surfaces require this; everything
// else is tenant-default. No fine-grained permission gating exists yet (D25 open).
export function isOps(snapshot: AuthSnapshot): boolean {
  return snapshot.roles.includes('dis:ops')
}
