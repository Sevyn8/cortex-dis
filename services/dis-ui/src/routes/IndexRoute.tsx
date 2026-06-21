import { Dashboard } from './Dashboard'

// The index route (`/`). Every persona lands on the one Dashboard. The earlier ops -> Ops Fleet
// redirect was removed when the Ops Fleet screen was retired: there is a single tenant-style
// Dashboard for all personas for now (an ops persona is cross-tenant with a null tenant_id, so
// the tenant-scoped Dashboard reads empty for them - accepted; the cross-tenant ops view is later).
export function IndexRoute() {
  return <Dashboard />
}
