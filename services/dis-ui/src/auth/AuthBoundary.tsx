import { Navigate, Outlet } from 'react-router'

import { useAuth } from './useAuth'

// Gates protected routes. While a stored token is being verified we render a
// minimal fallback. An unauthenticated user (no token, or a token that was
// expired, malformed, or had invalid claims, all handled in AuthProvider) is
// redirected to /dev/login.
//
// Real-mode seam (decisions.md D25): when Customer Master tokens replace the stub,
// an expired token surfaces as a dis-ui-server 401 and the UI is responsible for
// refresh. Refresh is deferred for this slice; an expired stub simply lands the
// user back at /dev/login, the dev analog of that re-auth flow.
export function AuthBoundary() {
  const { status } = useAuth()

  if (status === 'loading') {
    return <p>Loading...</p>
  }
  if (status === 'unauthenticated') {
    return <Navigate to="/dev/login" replace />
  }
  return <Outlet />
}
