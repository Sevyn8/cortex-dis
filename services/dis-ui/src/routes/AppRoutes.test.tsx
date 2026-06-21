import { screen } from '@testing-library/react'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

const tenantSnapshot: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}
const opsSnapshot: AuthSnapshot = {
  userId: 'u_opsdev0001',
  tenantId: null,
  storeId: null,
  roles: ['dis:ops', 'dis:read', 'dis:mapping_admin'],
}

function renderAt(path: string) {
  return renderWithProviders(<AppRoutes />, { snapshot: tenantSnapshot, initialEntries: [path] })
}

function renderAtAs(snapshot: AuthSnapshot, path: string) {
  return renderWithProviders(<AppRoutes />, { snapshot, initialEntries: [path] })
}

describe('AppRoutes', () => {
  it('renders the Tenant Dashboard at the index', async () => {
    renderAt('/')
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('still resolves /sources', async () => {
    renderAt('/sources')
    expect(await screen.findByRole('heading', { name: 'Manage sources' })).toBeInTheDocument()
  })

  it('resolves /notifications', async () => {
    renderAt('/notifications')
    expect(await screen.findByRole('heading', { name: 'Notifications' })).toBeInTheDocument()
  })

  it('resolves /sources/:sourceId/shadow', async () => {
    renderAt('/sources/manual_csv_upload/shadow')
    expect(
      await screen.findByRole('heading', { name: /Shadow review: manual_csv_upload/ }),
    ).toBeInTheDocument()
  })

  // The screens themselves are covered by their own screen tests; no placeholder
  // routes remain.

  it('renders the not-found state for an unknown route', async () => {
    renderAt('/no-such-page')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })

  it('lands an ops persona on the one Dashboard at the index (Ops Fleet removed, no redirect)', async () => {
    renderAtAs(opsSnapshot, '/')
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    // the retired Ops Fleet screen is gone for everyone
    expect(screen.queryByRole('heading', { name: 'Ops Fleet' })).not.toBeInTheDocument()
  })

  it('denies a non-ops persona on any /ops/* path (layout guard covers the subtree)', async () => {
    renderAt('/ops/something-else')
    expect(await screen.findByRole('alert')).toHaveTextContent(/access denied/i)
  })

  it('denies a non-ops persona on /ops/quarantine', async () => {
    renderAt('/ops/quarantine')
    expect(await screen.findByRole('alert')).toHaveTextContent(/access denied/i)
  })

  it('denies a non-ops persona on /ops/audit', async () => {
    renderAt('/ops/audit')
    expect(await screen.findByRole('alert')).toHaveTextContent(/access denied/i)
  })

  // T9: the retired fleet routes redirect to the canonical scope-aware screens for ops (no
  // 404). The redirects live inside OpsBoundary, so a non-ops persona is still denied (the
  // deny tests above still pass for /ops/quarantine and /ops/audit).
  it('redirects an ops persona from /ops/quarantine to the scope-aware Quarantine', async () => {
    renderAtAs(opsSnapshot, '/ops/quarantine')
    expect(await screen.findByRole('heading', { name: 'Quarantine' })).toBeInTheDocument()
  })

  it('redirects an ops persona from /ops/audit to the scope-aware Audit', async () => {
    renderAtAs(opsSnapshot, '/ops/audit')
    expect(
      await screen.findByRole('heading', { name: 'Audit and Trace Lookup' }),
    ).toBeInTheDocument()
  })
})
