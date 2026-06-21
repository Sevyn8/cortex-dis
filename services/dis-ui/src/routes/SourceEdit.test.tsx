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

describe('SourceEdit', () => {
  beforeEach(() => {
    __resetSourcesFixture()
  })

  it('prefills the form and shows source_id read-only (identity immutable)', async () => {
    renderAt('/sources/manual_csv_upload/edit')
    expect(await screen.findByRole('heading', { name: 'Edit source' })).toBeInTheDocument()
    // name is editable and prefilled
    expect(screen.getByLabelText('Name')).toHaveValue('Manual CSV Upload')
    // source_id is shown but NOT an editable field (no labeled source-id input)
    expect(screen.getByText('manual_csv_upload')).toBeInTheDocument()
    expect(screen.queryByLabelText('Source id')).not.toBeInTheDocument()
  })

  it('updates the display metadata and the index reflects it', async () => {
    const user = userEvent.setup()
    renderAt('/sources/manual_csv_upload/edit')
    const nameInput = await screen.findByLabelText('Name')
    await user.clear(nameInput)
    await user.type(nameInput, 'Renamed CSV')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('heading', { name: 'Manage sources' })).toBeInTheDocument()
    expect(screen.getByText('Renamed CSV')).toBeInTheDocument()
  })

  it('offers the Deprecate action here (T6: per-source manage home, not the list)', async () => {
    renderAt('/sources/manual_csv_upload/edit')
    expect(await screen.findByRole('heading', { name: 'Edit source' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Deprecate source' })).toBeInTheDocument()
    // FM1: soft transition only, no hard delete
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
  })

  it('deprecates the source via the confirm dialog (soft transition), then lands on the list', async () => {
    const user = userEvent.setup()
    renderAt('/sources/manual_csv_upload/edit')
    await screen.findByRole('heading', { name: 'Edit source' })
    await user.click(screen.getByRole('button', { name: 'Deprecate source' }))
    await user.click(await screen.findByRole('button', { name: 'Confirm deprecate' }))
    // navigates back to the sources list, where the source now reads deprecated
    expect(await screen.findByRole('heading', { name: 'Manage sources' })).toBeInTheDocument()
    expect(await screen.findByText('deprecated')).toBeInTheDocument()
  })
})
