import { isOps } from '../auth/AuthSnapshot'
import { useAuth } from '../auth/useAuth'
import { FleetQuarantineView } from './FleetQuarantineView'
import { TenantQuarantineView } from './TenantQuarantineView'

// Quarantine Console (surface map screen 7), ONE scope-aware route (/quarantine, slice 25 / T9).
// A thin dispatcher into two self-contained views (each owns its own hooks, the React-correct way
// to branch data sources):
// - TENANT mode: the real slice-15a endpoints (GET /quarantine[/{id}]), scope locked to the
//   caller's tenant server-side. Filters drive server-side query params; no resolve/resubmit
//   action exists (D82); original_payload is deferred (null).
// - OPS mode (isOps): the fleet-wide cross-tenant view, still fixture-backed and DEFERRED on the
//   backend (D76 - no platform see-all endpoint yet), with the Tenant column/filter + resubmit.
//
// AUTHORIZATION: fleet scope is requested ONLY for an ops user; the tenant view keys strictly on
// the caller's tenant. This UI gating is necessary BUT NOT SUFFICIENT - the real boundary is the
// server, which RLS-scopes tenant queries and must refuse fleet scope for a non-ops token.
export function QuarantineConsole() {
  const { snapshot } = useAuth()
  const ops = snapshot !== null && isOps(snapshot)
  return ops ? <FleetQuarantineView /> : <TenantQuarantineView snapshot={snapshot} />
}
