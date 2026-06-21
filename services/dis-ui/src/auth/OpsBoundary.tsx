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
  const { snapshot } = useAuth()
  const ops = snapshot !== null && isOps(snapshot)
  return ops ? <Outlet /> : <PermissionDenied />
}
