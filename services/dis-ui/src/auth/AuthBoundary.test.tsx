import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { SignJWT } from 'jose'
import { MemoryRouter } from 'react-router'

import { ME_FIXTURES } from '../lib/dis-ui-server/fixtures'
import { AppRoutes } from '../routes/AppRoutes'
import { AuthProvider } from './AuthProvider'
import { STUB_AUDIENCE, STUB_ISSUER, STUB_SECRET } from './dev/devStubSecret'
import { PERSONAS } from './dev/personas'
import { signStubToken } from './dev/signStubToken'
import { writeToken } from './storage'

const KEY = new TextEncoder().encode(STUB_SECRET)

// The protected Home page fetches via TanStack Query, so the full route tree
// needs a QueryClientProvider.
function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

async function expiredToken(): Promise<string> {
  const past = Math.floor(Date.now() / 1000) - 60
  return new SignJWT({
    'https://sevyn8.com/user_id': 'u_acmeuser0001',
    'https://sevyn8.com/tenant_id': 't_acme9k2l1mn4',
    'https://sevyn8.com/store_id': 's_acme0001a4b7',
    'https://sevyn8.com/roles': ['dis:read'],
  })
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject('u_acmeuser0001')
    .setIssuedAt(past - 60)
    .setIssuer(STUB_ISSUER)
    .setAudience(STUB_AUDIENCE)
    .setExpirationTime(past)
    .sign(KEY)
}

// These exercise the dev-stub mode path (redirect to the local /dev/login picker).
// The Auth0-mode path (redirect to the hosted login via loginWithRedirect) is
// covered in AuthProvider.auth0.test.tsx with the SDK mocked.
describe('AuthBoundary', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubEnv('VITE_AUTH_MODE', 'dev-stub')
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('renders the protected page when a valid token is stored', async () => {
    writeToken(await signStubToken(PERSONAS[0]))
    renderAt('/')
    // After the index redirect to /sources, the shell header shows the profile
    // email (the retired Home greeting is gone); assert the protected shell rendered.
    const email = ME_FIXTURES[PERSONAS[0].sub].email
    expect(await screen.findByText(email)).toBeInTheDocument()
  })

  it('redirects to /dev/login when no token is stored', async () => {
    renderAt('/')
    expect(await screen.findByRole('heading', { level: 1, name: /dev login/i })).toBeInTheDocument()
  })

  it('redirects to /dev/login when the stored token is expired', async () => {
    writeToken(await expiredToken())
    renderAt('/')
    expect(await screen.findByRole('heading', { level: 1, name: /dev login/i })).toBeInTheDocument()
  })
})
