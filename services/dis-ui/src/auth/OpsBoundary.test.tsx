import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router'

import type { AuthSnapshot } from './AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { OpsBoundary } from './OpsBoundary'

// OpsBoundary fails closed: only a dis:ops snapshot reaches the routed ops surface;
// everyone else gets PermissionDenied. While roles resolve from the BFF (DIS step 2,
// Auth0 mode), it renders loading rather than flashing PermissionDenied.

const opsUser: AuthSnapshot = {
  userId: 'u_opsdev0001',
  tenantId: null,
  storeId: null,
  roles: ['dis:ops', 'dis:read'],
}
const tenantUser: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme',
  storeId: null,
  roles: ['dis:read'],
}

function renderBoundary(snapshot: AuthSnapshot | null, rolesResolving = false) {
  return renderWithProviders(
    <Routes>
      <Route element={<OpsBoundary />}>
        <Route index element={<p>ops surface body</p>} />
      </Route>
    </Routes>,
    { snapshot, initialEntries: ['/'], rolesResolving },
  )
}

describe('OpsBoundary', () => {
  it('admits an ops snapshot to the ops surface', () => {
    renderBoundary(opsUser)
    expect(screen.getByText('ops surface body')).toBeInTheDocument()
  })

  it('denies a non-ops (tenant) snapshot', () => {
    renderBoundary(tenantUser)
    expect(screen.getByRole('alert')).toHaveTextContent(/access denied/i)
    expect(screen.queryByText('ops surface body')).not.toBeInTheDocument()
  })

  it('denies a null snapshot (fail closed)', () => {
    renderBoundary(null)
    expect(screen.getByRole('alert')).toHaveTextContent(/access denied/i)
    expect(screen.queryByText('ops surface body')).not.toBeInTheDocument()
  })

  it('shows loading (not PermissionDenied) while roles are resolving', () => {
    renderBoundary({ userId: 'u_x', tenantId: null, storeId: null, roles: [] }, true)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('ops surface body')).not.toBeInTheDocument()
  })
})
