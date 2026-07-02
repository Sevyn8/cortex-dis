import { Outlet } from 'react-router'

import { PermissionDenied } from '../components/states/PermissionDenied'
import { isOps } from './AuthSnapshot'
import { useAuth } from './useAuth'

// Ops route-guard (slice 24). A layout route wrapping the whole /ops/* subtree, so
// every present and future ops screen inherits it. isOps is the only gate and it fails
// closed: a non-ops (or null) snapshot renders PermissionDenied instead of the ops
// surface; an ops snapshot falls through to the routed ops screen. This sits under
// AuthBoundary, so the user is already authenticated here.
export function OpsBoundary() {
  const { snapshot, rolesResolving } = useAuth()
  // Until the one-per-session BFF roles resolution completes (Auth0 mode, DIS step 2),
  // render loading rather than flashing PermissionDenied on not-yet-known roles.
  // dev-stub mode never resolves (roles come from the token), so this is false there.
  if (rolesResolving) {
    return <p>Loading...</p>
  }
  const ops = snapshot !== null && isOps(snapshot)
  return ops ? <Outlet /> : <PermissionDenied />
}
