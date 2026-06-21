import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import * as opsCrossTenant from '../lib/dis-ui-server/ops-cross-tenant'
import { __resetOpsCrossTenantFixture } from '../lib/dis-ui-server/ops-cross-tenant'
import { QUARANTINE_TRACE_IDS } from '../lib/dis-ui-server/quarantine'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

const ops: AuthSnapshot = {
  userId: 'u_opsdev0001',
  tenantId: null,
  storeId: null,
  roles: ['dis:ops', 'dis:read', 'dis:mapping_admin'],
}

// T9: the fleet view now lives on the canonical scope-aware route /quarantine (the component
// renders fleet mode for an ops user); the old /ops/quarantine route is a redirect to here.
function renderOpsQuarantine() {
  return renderWithProviders(<AppRoutes />, { snapshot: ops, initialEntries: ['/quarantine'] })
}

describe('Ops Quarantine (cross-tenant mode)', () => {
  beforeEach(() => {
    __resetOpsCrossTenantFixture()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the fleet-wide list with a Tenant column and tenant names', async () => {
    renderOpsQuarantine()
    expect(await screen.findByRole('columnheader', { name: 'Tenant' })).toBeInTheDocument()
    // tenant names appear in both the rows and the tenant-filter options
    expect(screen.getAllByText('Acme Retail').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Beta Stores').length).toBeGreaterThan(0)
  })

  it('narrows the fleet list by tenant', async () => {
    const user = userEvent.setup()
    renderOpsQuarantine()
    await screen.findByRole('columnheader', { name: 'Tenant' })
    await user.selectOptions(screen.getByLabelText('Tenant filter'), 'Beta Stores')
    expect(screen.getByRole('button', { name: opsCrossTenant.OPS_QUARANTINE_TRACE_IDS.betaCanonical })).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: QUARANTINE_TRACE_IDS.acmeCanonical }),
    ).not.toBeInTheDocument()
  })

  it('ops resubmit calls the mutation with the row tenant_id', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    vi.spyOn(opsCrossTenant, 'useOpsResubmit').mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof opsCrossTenant.useOpsResubmit>)
    renderOpsQuarantine()
    await user.click(await screen.findByRole('button', { name: QUARANTINE_TRACE_IDS.acmeCanonical }))
    await user.click(await screen.findByRole('button', { name: 'Resubmit' }))
    await user.click(await screen.findByRole('button', { name: 'Confirm resubmit' }))
    expect(mutate.mock.calls[0][0]).toEqual({
      resubmit_type: 'replay',
      parent_trace_id: QUARANTINE_TRACE_IDS.acmeCanonical,
      tenant_id: 't_acme9k2l1mn4',
    })
  })

  it('disables resubmit with the reason for a fleet row at the chain-depth cap', async () => {
    const user = userEvent.setup()
    renderOpsQuarantine()
    await user.click(await screen.findByRole('button', { name: QUARANTINE_TRACE_IDS.acmeNormalization }))
    const button = await screen.findByRole('button', { name: 'Resubmit' })
    expect(button).toBeDisabled()
    expect(screen.getByText(/Chain depth 3 reached/)).toBeInTheDocument()
  })
})
