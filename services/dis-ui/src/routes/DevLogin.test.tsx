import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

import { AuthProvider } from '../auth/AuthProvider'
import { PERSONAS } from '../auth/dev/personas'
import { signStubToken } from '../auth/dev/signStubToken'
import { ME_FIXTURES } from '../lib/dis-ui-server/fixtures'
import { AppRoutes } from './AppRoutes'

// The protected Home page fetches via TanStack Query, so the full route tree
// needs a QueryClientProvider.
function renderApp() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/dev/login']}>
          <AppRoutes />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('DevLogin persona switch', () => {
  beforeEach(async () => {
    localStorage.clear()
    // pick() now logs in with pre-supplied per-persona tokens from VITE_STUB_TOKEN_*
    // (no client-side minting). Mint valid dev-stub tokens for the test and expose
    // them via the env the UI reads.
    const tenant = PERSONAS.find((p) => p.id === 'tenant')
    const ops = PERSONAS.find((p) => p.id === 'ops')
    if (tenant === undefined || ops === undefined) {
      throw new Error('expected tenant and ops personas')
    }
    vi.stubEnv('VITE_STUB_TOKEN_TENANT', await signStubToken(tenant))
    vi.stubEnv('VITE_STUB_TOKEN_OPS', await signStubToken(ops))
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('signs in as one persona, then switches to another', async () => {
    const user = userEvent.setup()
    renderApp()

    // Sign in as the tenant persona; the shell header shows that user's profile email.
    await user.click(await screen.findByRole('button', { name: /tenant user/i }))
    expect(await screen.findByText(ME_FIXTURES['u_acmeuser0001'].email)).toBeInTheDocument()

    // Log out, then sign in as the ops persona; the header email reflects the switch.
    await user.click(screen.getByRole('button', { name: /log out/i }))
    await user.click(await screen.findByRole('button', { name: /ops \(dev only\)/i }))
    expect(await screen.findByText(ME_FIXTURES['u_opsdev0001'].email)).toBeInTheDocument()
  })
})
