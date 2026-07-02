import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router'

import type { AuthSnapshot } from './AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { AtlasBoundary } from './AtlasBoundary'

// AtlasBoundary fails closed (like OpsBoundary): only a super-admin snapshot reaches the routed
// console; everyone else gets PermissionDenied. The backend require_super_admin is the real gate.

const superAdmin: AuthSnapshot = {
  userId: 'u_superadmin01',
  tenantId: null,
  storeId: null,
  roles: ['atlas:schema:publish', 'dis:read'],
}
const ops: AuthSnapshot = {
  userId: 'u_opsdev0001',
  tenantId: null,
  storeId: null,
  roles: ['dis:ops', 'dis:read'],
}

function renderBoundary(snapshot: AuthSnapshot | null, rolesResolving = false) {
  return renderWithProviders(
    <Routes>
      <Route element={<AtlasBoundary />}>
        <Route index element={<p>atlas console body</p>} />
      </Route>
    </Routes>,
    { snapshot, initialEntries: ['/'], rolesResolving },
  )
}

describe('AtlasBoundary', () => {
  it('admits a super-admin snapshot to the console', () => {
    renderBoundary(superAdmin)
    expect(screen.getByText('atlas console body')).toBeInTheDocument()
  })

  it('denies an ops (non-super-admin) snapshot', () => {
    renderBoundary(ops)
    expect(screen.getByRole('alert')).toHaveTextContent(/access denied/i)
    expect(screen.queryByText('atlas console body')).not.toBeInTheDocument()
  })

  it('denies a null snapshot (fail closed)', () => {
    renderBoundary(null)
    expect(screen.getByRole('alert')).toHaveTextContent(/access denied/i)
    expect(screen.queryByText('atlas console body')).not.toBeInTheDocument()
  })

  it('shows loading (not PermissionDenied) while roles are resolving', () => {
    // DIS step 2: roles not yet known (BFF resolution in flight). The boundary must
    // render loading, never flash PermissionDenied, even though isSuperAdmin is false.
    renderBoundary({ userId: 'u_sa', tenantId: null, storeId: null, roles: [] }, true)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('atlas console body')).not.toBeInTheDocument()
  })
})
