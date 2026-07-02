import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { AuthContext } from '../auth/context'
import type { AuthContextValue } from '../auth/context'

// Test helper: wraps UI in a fresh QueryClient (retry off, so failures surface at
// once and no cache bleeds between tests) plus a synchronous AuthContext value
// (bypassing AuthProvider's async token verification) plus a MemoryRouter. Pass a
// snapshot to render as that authenticated user, or null for unauthenticated;
// pass initialEntries to control the starting route (e.g. for AppRoutes tests).
export function renderWithProviders(
  ui: ReactNode,
  opts: { snapshot: AuthSnapshot | null; initialEntries?: string[]; rolesResolving?: boolean },
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const authValue: AuthContextValue = {
    status: opts.snapshot === null ? 'unauthenticated' : 'authenticated',
    snapshot: opts.snapshot,
    // Default false: existing tests gate on the snapshot synchronously, unchanged.
    // A test passes true to assert the role boundaries' resolving/loading state.
    rolesResolving: opts.rolesResolving ?? false,
    login: () => Promise.resolve(),
    logout: () => {},
  }
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={authValue}>
        <MemoryRouter initialEntries={opts.initialEntries ?? ['/']}>{ui}</MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}
