import { createContext } from 'react'

import type { AuthSnapshot } from './AuthSnapshot'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export type AuthContextValue = {
  status: AuthStatus
  snapshot: AuthSnapshot | null
  // Begins a sign-in. In Auth0 mode this redirects to the hosted login and the
  // argument is ignored. In dev-stub mode it verifies and stores the given raw
  // token, then marks the user authenticated (rejecting on an invalid token).
  login: (rawToken?: string) => Promise<void>
  logout: () => void
}

// Kept in its own module (no component export) so the provider and hook files
// each export a single concern and stay clean under react-refresh lint rules.
export const AuthContext = createContext<AuthContextValue | null>(null)
