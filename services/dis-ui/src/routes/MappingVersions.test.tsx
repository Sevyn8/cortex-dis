import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}

// Rendered via AppRoutes so the :sourceId route param resolves.
function renderAt(sourceId: string) {
  return renderWithProviders(<AppRoutes />, {
    snapshot: tenant,
    initialEntries: [`/sources/${sourceId}/mappings`],
  })
}

describe('MappingVersions (read-only)', () => {
  it('renders the version list with status badges', async () => {
    renderAt('manual_csv_upload')
    expect(await screen.findByRole('heading', { name: /Mappings: manual_csv_upload/ })).toBeInTheDocument()
    expect(screen.getByText('ACTIVE')).toBeInTheDocument()
    expect(screen.getByText('DEPRECATED')).toBeInTheDocument()
    expect(screen.getByText('STAGED')).toBeInTheDocument()
  })

  it('distinguishes the active version from the deprecated one', async () => {
    renderAt('manual_csv_upload')
    await screen.findByRole('heading', { name: /Mappings:/ })
    // status now renders as a semantic StatusBadge (token classes; same green/neutral
    // intent) - selector-only update
    expect(screen.getByText('ACTIVE')).toHaveClass('text-success')
    expect(screen.getByText('DEPRECATED')).toHaveClass('text-muted-foreground')
  })

  it('opens the full immutable definition when a version is viewed', async () => {
    const user = userEvent.setup()
    renderAt('manual_csv_upload')
    await screen.findByRole('heading', { name: /Mappings:/ })
    // First "View" button corresponds to the first row (v3 staged).
    await user.click(screen.getAllByRole('button', { name: 'View' })[0])
    expect(await screen.findByRole('heading', { name: /Definition/ })).toBeInTheDocument()
    expect(screen.getByText(/sku_id/)).toBeInTheDocument() // mapping_rules visible
  })

  it('renders the empty state for an unknown source', async () => {
    renderAt('src_unknown')
    expect(await screen.findByRole('heading', { name: 'No mappings for this source' })).toBeInTheDocument()
  })
})
