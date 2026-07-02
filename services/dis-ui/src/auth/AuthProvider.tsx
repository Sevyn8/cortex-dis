import { Auth0Provider, useAuth0 } from '@auth0/auth0-react'
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { setAccessTokenGetter } from '../lib/dis-ui-server/accessToken'
import { getMyRoles } from '../lib/dis-ui-server/roles'
import type { AuthSnapshot } from './AuthSnapshot'
import { snapshotFromClaims } from './AuthSnapshot'
import { getAuth0Config } from './Auth0Config'
import { getAuthMode } from './authMode'
import { AuthContext } from './context'
import type { AuthContextValue, AuthStatus } from './context'
import { clearToken, readToken, writeToken } from './storage'
import { verifyToken } from './verifyToken'

// Top-level auth provider. Branches on the resolved mode: Auth0 (default, and the
// only option a production build can select) or the local dev-stub (dev build +
// explicit opt-in only). Both branches provide the same AuthContext shape, so
// everything downstream (useAuth, the boundaries, the request seam) is mode-agnostic.
export function AuthProvider({ children }: { children: ReactNode }) {
  return getAuthMode() === 'dev-stub' ? (
    <DevStubAuthProvider>{children}</DevStubAuthProvider>
  ) : (
    <Auth0AuthProvider>{children}</Auth0AuthProvider>
  )
}

// ----- Auth0 mode -------------------------------------------------------------

function Auth0AuthProvider({ children }: { children: ReactNode }) {
  const config = getAuth0Config()
  return (
    <Auth0Provider
      domain={config.domain}
      clientId={config.clientId}
      authorizationParams={{
        // A registered callback on the Auth0 application; the app runs on :5173 in dev.
        redirect_uri: window.location.origin,
        audience: config.audience,
      }}
      // Tokens live IN MEMORY (never localStorage): the API seam reads them at
      // request time via getAccessTokenSilently (wired in Auth0AuthBridge).
      cacheLocation="memory"
    >
      <Auth0AuthBridge>{children}</Auth0AuthBridge>
    </Auth0Provider>
  )
}

function Auth0AuthBridge({ children }: { children: ReactNode }) {
  const {
    isLoading,
    isAuthenticated,
    user,
    error,
    getAccessTokenSilently,
    loginWithRedirect,
    logout,
  } = useAuth0()

  // Register the in-memory token getter DURING RENDER (not in an effect): child
  // effects run before parent effects, so a child query could call getAccessToken
  // before a parent effect had run. Assigning here (idempotent) guarantees the
  // getter is present before any child mounts, so the seam never falls back to
  // localStorage in Auth0 mode.
  setAccessTokenGetter(() => getAccessTokenSilently())

  // The identity + status derived from the verified token claims (unchanged logic).
  // Fail-safe: any SDK error, or claims that don't map, leave the user
  // unauthenticated (AuthBoundary then redirects) - never half-authenticated.
  const base = useMemo<{ status: AuthStatus; snapshot: AuthSnapshot | null }>(() => {
    if (isLoading) {
      return { status: 'loading', snapshot: null }
    }
    if (isAuthenticated && error === undefined && user !== undefined) {
      try {
        return { status: 'authenticated', snapshot: snapshotFromClaims(user as Record<string, unknown>) }
      } catch {
        return { status: 'unauthenticated', snapshot: null }
      }
    }
    return { status: 'unauthenticated', snapshot: null }
  }, [isLoading, isAuthenticated, user, error])

  // DIS step 2: Customer Master issues no roles claim, so resolve the caller's DIS
  // roles from the BFF ONCE per authenticated session (Auth0 mode only). The result is
  // keyed by userId so a login/logout/user-change is "not yet resolved" WITHOUT a
  // synchronous setState in the effect body: resolution only ever setState()s in the
  // async callbacks. Kept in memory only, consistent with the token.
  const [resolved, setResolved] = useState<{ userId: string; roles: string[] } | null>(null)
  const authedUserId = base.snapshot?.userId ?? null

  useEffect(() => {
    if (authedUserId === null) {
      return // not authenticated (or logged out): nothing to resolve
    }
    let active = true
    getMyRoles()
      .then((res) => {
        // The BFF is itself fail-safe (resolved=false => roles hidden); honor it.
        if (active) setResolved({ userId: authedUserId, roles: res.resolved ? res.roles : [] })
      })
      .catch(() => {
        // Transport/other failure: fail-safe to no roles. The user stays
        // authenticated (tenant-default surfaces work); role surfaces stay hidden.
        if (active) setResolved({ userId: authedUserId, roles: [] })
      })
    return () => {
      active = false
    }
  }, [authedUserId])

  const value = useMemo<AuthContextValue>(() => {
    // Roles count as resolved only for the CURRENT user; otherwise they are still
    // being fetched (null) and the role boundaries render loading, not PermissionDenied.
    const resolvedRoles =
      resolved !== null && authedUserId !== null && resolved.userId === authedUserId
        ? resolved.roles
        : null
    const rolesResolving = base.snapshot !== null && resolvedRoles === null
    const snapshot: AuthSnapshot | null =
      base.snapshot === null
        ? null
        : {
            ...base.snapshot,
            // CM is authoritative; union with any token-claim roles (empty today),
            // mirroring the server's require_super_admin resolution.
            roles: [...new Set([...base.snapshot.roles, ...(resolvedRoles ?? [])])],
          }
    return {
      status: base.status,
      snapshot,
      rolesResolving,
      async login() {
        await loginWithRedirect()
      },
      logout() {
        void logout({ logoutParams: { returnTo: window.location.origin } })
      },
    }
  }, [base, resolved, authedUserId, loginWithRedirect, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// ----- Dev-stub mode (local dev only, behind the isDev + mode guard) ----------

function DevStubAuthProvider({ children }: { children: ReactNode }) {
  // Resolve the no-token case synchronously at init so the effect only ever runs
  // the async verification path (no synchronous setState in the effect body).
  const [status, setStatus] = useState<AuthStatus>(() =>
    readToken() === null ? 'unauthenticated' : 'loading',
  )
  const [snapshot, setSnapshot] = useState<AuthSnapshot | null>(null)

  // The request seam reads the stub token from localStorage in dev-stub mode.
  // Registered during render (same rationale as the Auth0 bridge).
  setAccessTokenGetter(() => {
    const stored = readToken()
    return stored === null || stored.length === 0
      ? Promise.reject(new Error('no dev-stub token'))
      : Promise.resolve(stored)
  })

  // On mount, restore a stored token if present. An invalid, expired, or
  // malformed token is cleared and the user is left unauthenticated; AuthBoundary
  // then redirects.
  useEffect(() => {
    const raw = readToken()
    if (raw === null) {
      return
    }
    let active = true
    verifyToken(raw)
      .then((restored) => {
        if (!active) {
          return
        }
        setSnapshot(restored)
        setStatus('authenticated')
      })
      .catch(() => {
        if (!active) {
          return
        }
        clearToken()
        setSnapshot(null)
        setStatus('unauthenticated')
      })
    return () => {
      active = false
    }
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      snapshot,
      // Dev-stub roles come from the persona token via snapshotFromClaims; there is
      // no BFF resolution, so the role boundaries never wait. UNCHANGED behavior.
      rolesResolving: false,
      async login(rawToken?: string) {
        if (rawToken === undefined) {
          throw new Error('dev-stub login requires a token')
        }
        const next = await verifyToken(rawToken)
        writeToken(rawToken)
        setSnapshot(next)
        setStatus('authenticated')
      },
      logout() {
        clearToken()
        setSnapshot(null)
        setStatus('unauthenticated')
      },
    }),
    [status, snapshot],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
