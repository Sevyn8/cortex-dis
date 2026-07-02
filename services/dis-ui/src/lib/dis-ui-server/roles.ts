import { getJson } from './client'
import type { MeRolesResponse } from './types'

// Resolve the signed-in user's DIS role strings from the BFF (DIS step 2). The BFF
// forwards the caller's bearer to Customer Master and maps grants to role strings
// server-side (reusing require_super_admin's mapping), so the SPA never calls CM
// directly and never duplicates the tuple->role table. Auth0 mode only; the auth
// bridge calls this once per session after authentication and merges the result
// into the in-memory snapshot (never persisted).
//
// The BFF is fail-safe by design (200 {roles: [], resolved: false} on a CM failure),
// so a successful fetch already encodes the degraded case; the caller additionally
// treats a thrown error as roles=[] (fail-safe) so a transport failure hides role
// surfaces without blocking authentication.
export async function getMyRoles(): Promise<MeRolesResponse> {
  return getJson<MeRolesResponse>('/api/v1/me/roles')
}
