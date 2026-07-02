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

// DIS step 2: the bridge resolves DIS roles from the BFF once per session. Mock the
// roles module so tests are deterministic (no fetch/network); the default resolves
// to no roles so the token-claim tests are unaffected.
const r = vi.hoisted(() => ({
  getMyRoles: vi.fn<() => Promise<{ roles: string[]; resolved: boolean }>>(async () => ({
    roles: [],
    resolved: true,
  })),
}))
vi.mock('../lib/dis-ui-server/roles', () => ({ getMyRoles: r.getMyRoles }))

const NS = 'https://sevyn8.com/'

function Consumer() {
  const { status, snapshot, rolesResolving, logout } = useAuth()
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="roles-resolving">{String(rolesResolving)}</span>
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
  r.getMyRoles.mockReset()
  r.getMyRoles.mockResolvedValue({ roles: [], resolved: true })
  setAccessTokenGetter(null)
})

afterEach(() => {
  vi.unstubAllEnvs()
  setAccessTokenGetter(null)
})

describe('AuthProvider (Auth0 mode)', () => {
  it('derives the snapshot from the namespaced ID-token claims', async () => {
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
    // Let the once-per-session roles resolution settle (default: no CM roles), then
    // the snapshot is the token claims unioned with the (empty) resolved set.
    await waitFor(() => expect(screen.getByTestId('roles-resolving')).toHaveTextContent('false'))
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

  // --- DIS step 2: BFF role resolution (Auth0 mode) ---

  it('merges BFF-resolved roles into the snapshot (roles absent from the token)', async () => {
    // The token carries no roles claim (as real CM tokens do not); the BFF resolves
    // atlas:schema:publish, which must appear in the snapshot for the UI gate.
    r.getMyRoles.mockResolvedValue({ roles: ['atlas:schema:publish'], resolved: true })
    h.state = {
      isLoading: false,
      isAuthenticated: true,
      error: undefined,
      user: { sub: 'auth0|sa', [`${NS}user_id`]: 'u_sa', [`${NS}roles`]: [] },
    }
    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('roles-resolving')).toHaveTextContent('false'))
    expect(JSON.parse(screen.getByTestId('snapshot').textContent ?? 'null').roles).toEqual([
      'atlas:schema:publish',
    ])
    expect(r.getMyRoles).toHaveBeenCalledTimes(1) // once per session
  })

  it('stays authenticated with empty roles when BFF resolution fails (fail-safe)', async () => {
    r.getMyRoles.mockRejectedValue(new Error('network down'))
    h.state = {
      isLoading: false,
      isAuthenticated: true,
      error: undefined,
      user: { sub: 'auth0|x', [`${NS}user_id`]: 'u_x', [`${NS}roles`]: [] },
    }
    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('roles-resolving')).toHaveTextContent('false'))
    // Fail-safe: authenticated (tenant surfaces work), role surfaces hidden (roles empty).
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(JSON.parse(screen.getByTestId('snapshot').textContent ?? 'null').roles).toEqual([])
  })

  it('honors the BFF resolved=false fail-safe signal (roles hidden)', async () => {
    r.getMyRoles.mockResolvedValue({ roles: [], resolved: false })
    h.state = {
      isLoading: false,
      isAuthenticated: true,
      error: undefined,
      user: { sub: 'auth0|x', [`${NS}user_id`]: 'u_x', [`${NS}roles`]: [] },
    }
    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('roles-resolving')).toHaveTextContent('false'))
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(JSON.parse(screen.getByTestId('snapshot').textContent ?? 'null').roles).toEqual([])
  })

  it('exposes rolesResolving=true while the BFF call is in flight', async () => {
    // A deferred getMyRoles that we resolve manually: rolesResolving is true until it settles.
    let resolveRoles: (v: { roles: string[]; resolved: boolean }) => void = () => {}
    r.getMyRoles.mockReturnValue(
      new Promise((resolve) => {
        resolveRoles = resolve
      }),
    )
    h.state = {
      isLoading: false,
      isAuthenticated: true,
      error: undefined,
      user: { sub: 'auth0|sa', [`${NS}user_id`]: 'u_sa', [`${NS}roles`]: [] },
    }
    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    )
    // Authenticated immediately (tenant surfaces work) but roles still resolving.
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(screen.getByTestId('roles-resolving')).toHaveTextContent('true')
    resolveRoles({ roles: ['atlas:schema:publish'], resolved: true })
    await waitFor(() => expect(screen.getByTestId('roles-resolving')).toHaveTextContent('false'))
    expect(JSON.parse(screen.getByTestId('snapshot').textContent ?? 'null').roles).toEqual([
      'atlas:schema:publish',
    ])
  })
})
