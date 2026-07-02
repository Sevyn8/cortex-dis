import { Outlet } from 'react-router'

import { PermissionDenied } from '../components/states/PermissionDenied'
import { isSuperAdmin } from './AuthSnapshot'
import { useAuth } from './useAuth'

// Atlas console route-guard (A4 PR3b), modelled exactly on OpsBoundary. A layout route
// wrapping the whole /atlas/* subtree, so every present and future Atlas console screen
// inherits it. isSuperAdmin is the only gate and it fails closed: a non-super-admin (or
// null) snapshot renders PermissionDenied instead of the console; a super-admin snapshot
// falls through to the routed screen. This sits under AuthBoundary, so the user is already
// authenticated here. The backend require_super_admin (dis-ui-server) is the REAL gate;
// this boundary is UX (it hides a surface the BFF would 403 anyway).
export function AtlasBoundary() {
  const { snapshot, rolesResolving } = useAuth()
  // Roles are resolved from the BFF once per session (Auth0 mode, DIS step 2). Until
  // that completes, render a loading state rather than flashing PermissionDenied on a
  // snapshot whose roles are not yet known. dev-stub mode never resolves (roles come
  // from the token), so rolesResolving is always false there.
  if (rolesResolving) {
    return <p>Loading...</p>
  }
  const allowed = snapshot !== null && isSuperAdmin(snapshot)
  return allowed ? (
    <Outlet />
  ) : (
    <PermissionDenied message="The Atlas console is Super Admin only." />
  )
}
