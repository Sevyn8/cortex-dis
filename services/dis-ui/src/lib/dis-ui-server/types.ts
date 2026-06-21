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
