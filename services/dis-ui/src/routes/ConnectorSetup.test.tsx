import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

// Drives the unified "Connect a System" wizard (/connectors/new) with userEvent. The POS branch
// is fully stubbed (connectors-api); the CSV branch wires two GETs (template-types,
// template-mapping-fields?template_type=) which are fixture-backed in tests, with upload/create/
// preview stubbed. Focus: both branch steppers + the per-row IGNORE behavior.

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

function renderWizard() {
  return renderWithProviders(<AppRoutes />, {
    snapshot: tenant,
    initialEntries: ['/connectors/new'],
  })
}

describe('ConnectorSetup (Live Sync wizard)', () => {
  it('renders the unified Source step with POS tiles AND a CSV/SFTP tile', async () => {
    renderWizard()
    expect(await screen.findByRole('heading', { name: 'Connect a system' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Shopify' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Square' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Clover' })).toBeInTheDocument()
    // Chunk 2: the unified surface now also offers the CSV / SFTP upload branch.
    expect(screen.getByRole('radio', { name: 'CSV / SFTP' })).toBeInTheDocument()
    // The two groups are labeled separately.
    expect(screen.getByRole('radiogroup', { name: 'Live sync connector' })).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'File upload source' })).toBeInTheDocument()
    // Source step cannot advance until a tile is picked.
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('advances through all 8 steps to the live confirmation (stubbed, no network)', async () => {
    const user = userEvent.setup()
    renderWizard()
    await screen.findByRole('heading', { name: 'Connect a system' })

    // 1. Source
    await user.click(screen.getByRole('radio', { name: 'Shopify' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 2. Connect: name + domain, then authorize (stub advances).
    await user.type(screen.getByLabelText('Source name'), 'Shopify sales')
    await user.type(screen.getByLabelText('Shopify store domain'), 'acme.myshopify.com')
    await user.click(screen.getByRole('button', { name: 'Sign in with Shopify' }))

    // 3. Authorized
    expect(await screen.findByText('Connected to Shopify')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // 4. Locations: check a location and map it to a DIS store.
    const checkbox = await screen.findByRole('checkbox', { name: 'Sync Downtown Flagship' })
    await user.click(checkbox)
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'DIS store for Downtown Flagship' }),
      'Acme Downtown #1',
    )
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 5. Data & sync: 'Orders' is selected by default.
    expect(await screen.findByRole('checkbox', { name: 'Orders' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 6. AI mapping: ignore the unmapped 'gateway' field so all active fields are mapped.
    expect(await screen.findByText('order_id')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Ignore gateway' }))
    await user.click(screen.getByRole('button', { name: 'Continue to preview' }))

    // 7. Preview: the template-type field + the canonical table.
    expect(await screen.findByLabelText('Template type')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Go live' }))

    // 8. Live confirmation.
    expect(await screen.findByRole('status')).toHaveTextContent('Shopify is live')
    expect(screen.getByRole('link', { name: 'Done' })).toBeInTheDocument()
  })

  it('IGNORE toggle dims a row, badges it Ignored, and unblocks Continue', async () => {
    const user = userEvent.setup()
    renderWizard()
    await screen.findByRole('heading', { name: 'Connect a system' })

    // Fast path to the mapping step.
    await user.click(screen.getByRole('radio', { name: 'Shopify' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.type(screen.getByLabelText('Source name'), 'Shopify sales')
    await user.type(screen.getByLabelText('Shopify store domain'), 'acme.myshopify.com')
    await user.click(screen.getByRole('button', { name: 'Sign in with Shopify' }))
    await user.click(await screen.findByRole('button', { name: 'Continue' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Sync Downtown Flagship' }))
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'DIS store for Downtown Flagship' }),
      'Acme Downtown #1',
    )
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.click(await screen.findByRole('button', { name: 'Next' }))

    // At the mapping step: 'gateway' is unmapped, so Continue is blocked.
    await screen.findByText('gateway')
    const ignoreGateway = screen.getByRole('checkbox', { name: 'Ignore gateway' })
    expect(screen.getByRole('button', { name: 'Continue to preview' })).toBeDisabled()
    expect(screen.queryByText('Ignored')).not.toBeInTheDocument()

    // Ignoring it badges the row Ignored and unblocks Continue.
    await user.click(ignoreGateway)
    expect(ignoreGateway).toBeChecked()
    expect(screen.getByText('Ignored')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue to preview' })).toBeEnabled()
  })
})

describe('ConnectorSetup (CSV / SFTP branch)', () => {
  it('walks Source -> Upload -> Template type -> AI mapping (real parse) -> Preview -> Created and live', async () => {
    const user = userEvent.setup()
    renderWizard()
    await screen.findByRole('heading', { name: 'Connect a system' })

    // 1. Source: pick the CSV / SFTP tile (CSV branch).
    await user.click(screen.getByRole('radio', { name: 'CSV / SFTP' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 2. Upload: name + a REAL multi-column CSV file (the file is actually parsed at mapping).
    await user.type(screen.getByLabelText('Source name'), 'Weekly export')
    const file = new File(
      ['item_code,qty,sold_at\nSKU-1,2,31-12-2025\nSKU-2,1,01-01-2026'],
      'sales.csv',
      { type: 'text/csv' },
    )
    await user.upload(screen.getByLabelText('CSV file'), file)
    // Fix 3: the selected-file card shows the REAL size (not the old 0-byte File([]) stub).
    expect(screen.queryByText('0 B')).not.toBeInTheDocument()
    expect(screen.getByText(/\d+ B/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 3. Template type (WIRED, fixture-backed): the three types render; pick Sales.
    expect(await screen.findByRole('radio', { name: 'Sales' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Inventory change' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Catalogue snapshot' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled() // until a type is chosen
    await user.click(screen.getByRole('radio', { name: 'Sales' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 4. AI mapping: the REAL parsed columns render (from the uploaded CSV headers), targets from
    // the type-aware sales catalog. The template-level Number locale picker is present; ignore a
    // column (Ignored badge). Continue is enabled (every non-ignored column has a target).
    expect(await screen.findByText('item_code')).toBeInTheDocument()
    expect(screen.getByText('sold_at')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Number locale' })).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Number locale' }), 'eu')
    await user.click(screen.getByRole('checkbox', { name: 'Ignore item_code' }))
    expect(screen.getByText('Ignored')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Continue to preview' }))

    // 5. Preview: the template type is shown READ-ONLY (chosen earlier, no re-pick).
    expect(await screen.findByText('Chosen earlier; not editable here.')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Template type' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create template' }))

    // 6. Template created: slice-16c create-as-ACTIVE (fixture mirrors real: active v1, no draft),
    // so the copy is "Created and live" with an Active v1 badge.
    expect(await screen.findByRole('status')).toHaveTextContent('Created and live')
    expect(screen.getByText('Active v1')).toBeInTheDocument()
    expect(screen.queryByText('Submitted')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Done' })).toBeInTheDocument()
  })
})
