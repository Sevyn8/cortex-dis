import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type { AuthSnapshot } from './AuthSnapshot'
import { AuthContext } from './context'
import type { AuthContextValue, AuthStatus } from './context'
import { clearToken, readToken, writeToken } from './storage'
import { verifyToken } from './verifyToken'

export function AuthProvider({ children }: { children: ReactNode }) {
  // Resolve the no-token case synchronously at init so the effect only ever runs
  // the async verification path (no synchronous setState in the effect body).
  const [status, setStatus] = useState<AuthStatus>(() =>
    readToken() === null ? 'unauthenticated' : 'loading',
  )
  const [snapshot, setSnapshot] = useState<AuthSnapshot | null>(null)

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
      async login(rawToken: string) {
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
