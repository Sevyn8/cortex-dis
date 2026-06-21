import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { analyzeCsvSample } from '../lib/dis-ui-server/connectors-api'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

// Fix (AI-mapping step): a parse / suggestions failure must show an error with a retry, not hang
// on a perpetual spinner. Only analyzeCsvSample is mocked (to reject); the catalog still loads.
vi.mock('../lib/dis-ui-server/connectors-api', async (orig) => ({
  ...(await orig<typeof import('../lib/dis-ui-server/connectors-api')>()),
  analyzeCsvSample: vi.fn(),
}))

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

afterEach(() => vi.clearAllMocks())

describe('ConnectorSetup CSV branch: analyze error', () => {
  it('shows an error with a retry instead of an infinite spinner', async () => {
    vi.mocked(analyzeCsvSample).mockRejectedValue(new Error('papaparse exploded'))
    const user = userEvent.setup()
    renderWithProviders(<AppRoutes />, { snapshot: tenant, initialEntries: ['/connectors/new'] })
    await screen.findByRole('heading', { name: 'Connect a system' })

    await user.click(screen.getByRole('radio', { name: 'CSV / SFTP' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))

    await user.type(screen.getByLabelText('Source name'), 'Weekly export')
    const file = new File(['item_code,qty\nSKU-1,2'], 'sales.csv', { type: 'text/csv' })
    await user.upload(screen.getByLabelText('CSV file'), file)
    await user.click(screen.getByRole('button', { name: 'Next' }))

    await user.click(await screen.findByRole('radio', { name: 'Sales' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // The analyze fails: an alert is shown (not the perpetual loading label), with a retry.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/valid CSV/i)
    expect(
      screen.queryByText('Reading your file and suggesting field mappings...'),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
