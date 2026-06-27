import { useEffect } from 'react'
import { Navigate, Outlet } from 'react-router'

import { getAuthMode } from './authMode'
import { useAuth } from './useAuth'

// Gates protected routes. While auth resolves we render a minimal fallback. An
// unauthenticated user (no/expired/malformed token, or an SDK error - all handled
// in AuthProvider) is sent to sign in. Fail-safe: there is no half-authenticated
// state; anything short of 'authenticated' never renders the protected outlet.
//
// In Auth0 mode "sign in" means redirecting to the Auth0 hosted login
// (loginWithRedirect, exposed as login()); we trigger it and show a minimal
// fallback while the redirect happens. In dev-stub mode it means navigating to the
// local /dev/login persona picker (unchanged).
export function AuthBoundary() {
  const { status, login } = useAuth()
  const mode = getAuthMode()

  useEffect(() => {
    if (status === 'unauthenticated' && mode === 'auth0') {
      void login()
    }
  }, [status, mode, login])

  if (status === 'loading') {
    return <p>Loading...</p>
  }
  if (status === 'unauthenticated') {
    return mode === 'auth0' ? <p>Signing in...</p> : <Navigate to="/dev/login" replace />
  }
  return <Outlet />
}
