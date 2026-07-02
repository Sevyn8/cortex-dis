// The dis-ui-server GET /me response: the signed-in user's display profile.
//
// This models a future dis-ui-server -> Customer Master profile call. It is OPEN,
// NOT a confirmed endpoint: architecture 4.17 lists no GET /me handler, and
// attribute-needs.md (the identity-service needs doc) explicitly routes the user's
// email / name / display fields to a separate dis-ui-server -> Customer Master
// call, not the data-plane identity-service. These fields are NOT token claims;
// the token carries only sub / tenant_id / store_id / roles (see AuthSnapshot).
export type MeResponse = {
  user_id: string
  email: string
  name: string
  tenant_id: string | null
  // Display name of the tenant; null for ops users (cross-tenant, no single tenant).
  tenant_name: string | null
}

// The dis-ui-server GET /me/roles response: the caller's resolved DIS role strings
// (DIS step 2). Customer Master issues no roles claim on the token, so the SPA
// resolves roles through the BFF, which maps CM grants to role strings server-side
// (the tuple->role mapping is NOT duplicated here). `resolved` is the fail-safe
// signal: false means the BFF could not resolve (CM unavailable/unset) and returned
// an empty set, so the SPA hides role surfaces while tenant-default surfaces work.
export type MeRolesResponse = {
  roles: string[]
  resolved: boolean
}
