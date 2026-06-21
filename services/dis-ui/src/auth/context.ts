import { createContext } from 'react'

import type { AuthSnapshot } from './AuthSnapshot'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export type AuthContextValue = {
  status: AuthStatus
  snapshot: AuthSnapshot | null
  // Verifies and stores a raw token, then marks the user authenticated. Rejects
  // (and changes nothing) if the token is invalid.
  login: (rawToken: string) => Promise<void>
  logout: () => void
}

// Kept in its own module (no component export) so the provider and hook files
// each export a single concern and stay clean under react-refresh lint rules.
export const AuthContext = createContext<AuthContextValue | null>(null)
