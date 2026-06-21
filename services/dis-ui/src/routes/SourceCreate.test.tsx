import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { __resetSourcesFixture } from '../lib/dis-ui-server/sources'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

function renderAt(path: string) {
  return renderWithProviders(<AppRoutes />, { snapshot: tenant, initialEntries: [path] })
}

describe('SourceCreate', () => {
  beforeEach(() => {
    __resetSourcesFixture()
  })

  it('renders the create form', async () => {
    renderAt('/sources/new')
    expect(await screen.findByRole('heading', { name: 'New source' })).toBeInTheDocument()
    expect(screen.getByLabelText('Source id')).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
    expect(screen.getByLabelText('Type')).toBeInTheDocument()
    expect(screen.getByLabelText('Store')).toBeInTheDocument()
  })

  it('requires a source id', async () => {
    const user = userEvent.setup()
    renderAt('/sources/new')
    await screen.findByRole('heading', { name: 'New source' })
    await user.click(screen.getByRole('button', { name: 'Create source' }))
    expect(screen.getByRole('alert')).toHaveTextContent(/source id is required/i)
    // still on the create page (no navigation)
    expect(screen.getByRole('heading', { name: 'New source' })).toBeInTheDocument()
  })

  it('creates a source and the index reflects it', async () => {
    const user = userEvent.setup()
    renderAt('/sources/new')
    await screen.findByRole('heading', { name: 'New source' })
    await user.type(screen.getByLabelText('Source id'), 'square_pos')
    await user.type(screen.getByLabelText('Name'), 'Square POS')
    await user.click(screen.getByRole('button', { name: 'Create source' }))
    // navigates to the index, which shows the new source
    expect(await screen.findByRole('heading', { name: 'Manage sources' })).toBeInTheDocument()
    expect(screen.getByText('Square POS')).toBeInTheDocument()
  })
})
