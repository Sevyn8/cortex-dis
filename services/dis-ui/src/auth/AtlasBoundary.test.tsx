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

function renderBoundary(snapshot: AuthSnapshot | null) {
  return renderWithProviders(
    <Routes>
      <Route element={<AtlasBoundary />}>
        <Route index element={<p>atlas console body</p>} />
      </Route>
    </Routes>,
    { snapshot, initialEntries: ['/'] },
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
})
