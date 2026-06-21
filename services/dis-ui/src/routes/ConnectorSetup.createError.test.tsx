import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { DisUiServerHttpError } from '../lib/dis-ui-server/client'
import { createCsvTemplate } from '../lib/dis-ui-server/connectors-api'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

// Fix 2: a create failure (the semantic gate's 400) must be surfaced on the Preview step, not
// thrown as a console-only unhandled rejection. Only createCsvTemplate is mocked; the rest of the
// CSV branch (real parse + fixture suggestions) runs as in the happy-path test.
vi.mock('../lib/dis-ui-server/connectors-api', async (orig) => ({
  ...(await orig<typeof import('../lib/dis-ui-server/connectors-api')>()),
  createCsvTemplate: vi.fn(),
}))

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

const GATE_MESSAGE =
  "mapping_rules leave mandatory StoreSkuCurrentPosition column(s) ['currency', 'product_category', 'unit_cost'] unprovided; each must come from a rename or a derive"

afterEach(() => vi.clearAllMocks())

describe('ConnectorSetup CSV branch: create error', () => {
  it('renders the gate message on Preview and stays there (no unhandled throw, no advance)', async () => {
    vi.mocked(createCsvTemplate).mockRejectedValue(
      new DisUiServerHttpError(400, 'mapping_config', GATE_MESSAGE, {}),
    )
    const user = userEvent.setup()
    renderWithProviders(<AppRoutes />, { snapshot: tenant, initialEntries: ['/connectors/new'] })
    await screen.findByRole('heading', { name: 'Connect a system' })

    await user.click(screen.getByRole('radio', { name: 'CSV / SFTP' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))

    await user.type(screen.getByLabelText('Source name'), 'Weekly export')
    const file = new File(
      ['item_code,qty,sold_at\nSKU-1,2,31-12-2025\nSKU-2,1,01-01-2026'],
      'sales.csv',
      { type: 'text/csv' },
    )
    await user.upload(screen.getByLabelText('CSV file'), file)
    await user.click(screen.getByRole('button', { name: 'Next' }))

    await user.click(await screen.findByRole('radio', { name: 'Sales' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))

    await screen.findByText('item_code')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Number locale' }), 'eu')
    await user.click(screen.getByRole('checkbox', { name: 'Ignore item_code' }))
    await user.click(screen.getByRole('button', { name: 'Continue to preview' }))

    // Preview reached; submit the create, which the backend rejects with the semantic gate.
    await screen.findByText('Chosen earlier; not editable here.')
    await user.click(screen.getByRole('button', { name: 'Create template' }))

    // The error is rendered inline (the backend sentence, verbatim) with an action line.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not create this template')
    expect(alert).toHaveTextContent(GATE_MESSAGE)
    // Still on Preview: the create did NOT advance to the Created/live confirmation.
    expect(screen.getByRole('button', { name: 'Create template' })).toBeInTheDocument()
    expect(screen.queryByText('Created and live')).not.toBeInTheDocument()
  })
})
