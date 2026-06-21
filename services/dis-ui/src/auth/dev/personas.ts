// DEV ONLY. The personas offered at /dev/login. Each models the Customer Master
// token claim set Sanjeev's slice-2 fakes pin (sub, tenant_id, store_id, roles -
// PROVISIONAL pending decisions.md D25). Personas carry NO profile fields; email/
// name/tenant_name live in the separate profile fixtures (lib/dis-ui-server).

export type StubPersona = {
  id: string
  label: string
  sub: string
  // null for ops (cross-tenant). Ids use the t_*/s_*/u_* external form from
  // Sanjeev's fixtures; the external<->UUID translation is OPEN (decisions.md D37).
  tenant_id: string | null
  store_id: string | null
  roles: string[]
}

export const PERSONAS: StubPersona[] = [
  {
    // The real slice-2 fixture identity (libs/dis-testing fixtures.py).
    id: 'tenant',
    label: 'Tenant user (Acme Retail)',
    sub: 'u_acmeuser0001',
    tenant_id: 't_acme9k2l1mn4',
    store_id: 's_acme0001a4b7',
    roles: ['dis:upload', 'dis:read'],
  },
  {
    // DEV-ONLY construct: no ops user exists in Sanjeev's fixtures (seed.py seeds
    // tenants/stores only). Ops is cross-tenant, so tenant_id/store_id are null.
    id: 'ops',
    label: 'Ops (dev only)',
    sub: 'u_opsdev0001',
    tenant_id: null,
    store_id: null,
    roles: ['dis:ops', 'dis:read', 'dis:mapping_admin'],
  },
]
