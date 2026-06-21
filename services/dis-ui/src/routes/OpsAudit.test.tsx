import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { OPS_AUDIT_TRACE_IDS } from '../lib/dis-ui-server/ops-cross-tenant'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

const ops: AuthSnapshot = {
  userId: 'u_opsdev0001',
  tenantId: null,
  storeId: null,
  roles: ['dis:ops', 'dis:read', 'dis:mapping_admin'],
}

describe('Ops Audit (cross-tenant mode)', () => {
  it('looks up a cross-tenant trace and shows the tenant + lifecycle', async () => {
    const user = userEvent.setup()
    // T9: ops Audit now lives on the canonical /audit route (the component renders
    // cross-tenant mode for ops); /ops/audit redirects here.
    renderWithProviders(<AppRoutes />, { snapshot: ops, initialEntries: ['/audit'] })

    await user.type(screen.getByLabelText('Trace ID'), OPS_AUDIT_TRACE_IDS.betaHealthy)
    await user.click(screen.getByRole('button', { name: /look up/i }))

    // tenant field on the cross-tenant result + the lifecycle stages
    expect(await screen.findByText(/Beta Stores/)).toBeInTheDocument()
    expect(screen.getByText('received')).toBeInTheDocument()
    expect(screen.getByText('committed')).toBeInTheDocument()
  })
})
