import { createContext } from 'react'

import type { AuthSnapshot } from './AuthSnapshot'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export type AuthContextValue = {
  status: AuthStatus
  snapshot: AuthSnapshot | null
  // True while the one-per-session BFF roles resolution is in flight (Auth0 mode,
  // DIS step 2). The user is already `authenticated` (tenant-default surfaces work);
  // only the role gates (AtlasBoundary/OpsBoundary) wait on this, rendering a loading
  // state instead of a PermissionDenied flash until roles are known. Always false in
  // dev-stub mode (roles come from the persona token, no BFF call).
  rolesResolving: boolean
  // Begins a sign-in. In Auth0 mode this redirects to the hosted login and the
  // argument is ignored. In dev-stub mode it verifies and stores the given raw
  // token, then marks the user authenticated (rejecting on an invalid token).
  login: (rawToken?: string) => Promise<void>
  logout: () => void
}

// Kept in its own module (no component export) so the provider and hook files
// each export a single concern and stay clean under react-refresh lint rules.
export const AuthContext = createContext<AuthContextValue | null>(null)
