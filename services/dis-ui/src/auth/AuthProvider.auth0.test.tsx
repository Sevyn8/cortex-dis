import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getAccessToken, setAccessTokenGetter } from '../lib/dis-ui-server/accessToken'
import { AuthBoundary } from './AuthBoundary'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './useAuth'

// Mock the Auth0 SDK: Auth0Provider passes children through (no real init/network),
// useAuth0 returns a per-test mutable state + spies. vi.hoisted so the factory (which
// vitest hoists above imports) can reference the shared handles.
const h = vi.hoisted(() => ({
  state: {
    isLoading: false,
    isAuthenticated: false,
    user: undefined as Record<string, unknown> | undefined,
    error: undefined as Error | undefined,
  },
  loginWithRedirect: vi.fn(async () => {}),
  logout: vi.fn(),
  getAccessTokenSilently: vi.fn(async () => 'sdk-token'),
}))

vi.mock('@auth0/auth0-react', () => ({
  Auth0Provider: ({ children }: { children: ReactNode }) => children,
  useAuth0: () => ({
    isLoading: h.state.isLoading,
    isAuthenticated: h.state.isAuthenticated,
    user: h.state.user,
    error: h.state.error,
    loginWithRedirect: h.loginWithRedirect,
    logout: h.logout,
    getAccessTokenSilently: h.getAccessTokenSilently,
  }),
}))

const NS = 'https://sevyn8.com/'

function Consumer() {
  const { status, snapshot, logout } = useAuth()
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="snapshot">{JSON.stringify(snapshot)}</span>
      <button type="button" onClick={() => logout()}>
        Log out
      </button>
    </div>
  )
}

beforeEach(() => {
  // Auth0 mode requires the SPA config; supply the public values via env.
  vi.stubEnv('VITE_AUTH_MODE', 'auth0')
  vi.stubEnv('VITE_AUTH0_DOMAIN', 'sevyn8.us.auth0.com')
  vi.stubEnv('VITE_AUTH0_CLIENT_ID', 'test-client-id')
  vi.stubEnv('VITE_AUTH0_AUDIENCE', 'https://api.cortex.sevyn8.com')
  h.state = { isLoading: false, isAuthenticated: false, user: undefined, error: undefined }
  h.loginWithRedirect.mockClear()
  h.logout.mockClear()
  h.getAccessTokenSilently.mockClear()
  setAccessTokenGetter(null)
})

afterEach(() => {
  vi.unstubAllEnvs()
  setAccessTokenGetter(null)
})

describe('AuthProvider (Auth0 mode)', () => {
  it('derives the snapshot from the namespaced ID-token claims', () => {
    h.state = {
      isLoading: false,
      isAuthenticated: true,
      error: undefined,
      user: {
        sub: 'auth0|abc123',
        [`${NS}user_id`]: '019e5e3c-b5d3-705f-9002-2451c4ca2626',
        [`${NS}tenant_id`]: 't_acme',
        [`${NS}store_id`]: 's_acme',
        [`${NS}roles`]: ['dis:read', 'dis:ops'],
      },
    }
    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    )
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(JSON.parse(screen.getByTestId('snapshot').textContent ?? 'null')).toEqual({
      userId: '019e5e3c-b5d3-705f-9002-2451c4ca2626',
      tenantId: 't_acme',
      storeId: 's_acme',
      roles: ['dis:read', 'dis:ops'],
    })
  })

  it('wires the in-memory access-token seam to getAccessTokenSilently', async () => {
    h.state = {
      isLoading: false,
      isAuthenticated: true,
      error: undefined,
      user: { sub: 'auth0|abc', [`${NS}user_id`]: 'u1', [`${NS}roles`]: [] },
    }
    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    )
    // The seam (what the API client reads) resolves to the SDK's in-memory token.
    await expect(getAccessToken()).resolves.toBe('sdk-token')
    expect(h.getAccessTokenSilently).toHaveBeenCalled()
  })

  it('treats an SDK error as unauthenticated (fail-safe, no half-auth)', () => {
    h.state = {
      isLoading: false,
      isAuthenticated: false,
      error: new Error('login_required'),
      user: undefined,
    }
    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    )
    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
    expect(screen.getByTestId('snapshot')).toHaveTextContent('null')
  })

  it('logout calls Auth0 logout with returnTo the origin', async () => {
    h.state = {
      isLoading: false,
      isAuthenticated: true,
      error: undefined,
      user: { sub: 'auth0|abc', [`${NS}user_id`]: 'u1', [`${NS}roles`]: [] },
    }
    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: /log out/i }))
    expect(h.logout).toHaveBeenCalledWith({ logoutParams: { returnTo: window.location.origin } })
  })

  it('AuthBoundary redirects an unauthenticated user to the Auth0 hosted login', async () => {
    h.state = { isLoading: false, isAuthenticated: false, error: undefined, user: undefined }
    render(
      <AuthProvider>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<AuthBoundary />}>
              <Route index element={<p>protected</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>,
    )
    await waitFor(() => expect(h.loginWithRedirect).toHaveBeenCalled())
    expect(screen.queryByText('protected')).not.toBeInTheDocument()
    expect(screen.getByText(/signing in/i)).toBeInTheDocument()
  })
})
