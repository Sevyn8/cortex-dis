import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { __resetMappingsFixture } from '../lib/dis-ui-server/mappings'
import * as shadow from '../lib/dis-ui-server/shadow'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

// Rendered via AppRoutes so the :sourceId param resolves and the screen shares one
// QueryClient with MappingVersions when we navigate there to check invalidation.
function renderAt(path: string) {
  return renderWithProviders(<AppRoutes />, { snapshot: tenant, initialEntries: [path] })
}

describe('Shadow Rollout Review (tenant slice)', () => {
  beforeEach(() => {
    __resetMappingsFixture()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders stats and a diff sample', async () => {
    renderAt('/sources/manual_csv_upload/shadow')
    expect(await screen.findByRole('heading', { name: /Shadow review: manual_csv_upload/ })).toBeInTheDocument()
    // stats now render as metric cards (label + value); same values - selector-only
    expect(screen.getByText('last 48h')).toBeInTheDocument()
    expect(screen.getByText(/99.4%/)).toBeInTheDocument()
    // a diff sample row on the canonical column
    expect(await screen.findByText('A123')).toBeInTheDocument()
    expect(screen.getAllByText('source_sale_timestamp').length).toBeGreaterThan(0)
  })

  it('promote settles to the no-staged empty state and MappingVersions reflects it', async () => {
    const user = userEvent.setup()
    renderAt('/sources/manual_csv_upload/shadow')
    await screen.findByRole('heading', { name: /Shadow review/ })
    await user.click(screen.getByRole('button', { name: 'Promote to active' }))
    // shadow query refetches -> no staged -> empty state
    expect(await screen.findByRole('heading', { name: 'No staged version' })).toBeInTheDocument()

    // MappingVersions reads the mutated store and shows the new state
    cleanup()
    renderAt('/sources/manual_csv_upload/mappings')
    await screen.findByRole('heading', { name: /Mappings:/ })
    expect(screen.getByText('ACTIVE')).toHaveClass('text-success') // semantic StatusBadge token
    // v2 is now deprecated and v3 active: exactly one active badge
    expect(screen.getAllByText('ACTIVE')).toHaveLength(1)
    expect(screen.getAllByText('DEPRECATED').length).toBe(2)
  })

  it('reject deprecates the staged version, active untouched', async () => {
    const user = userEvent.setup()
    renderAt('/sources/manual_csv_upload/shadow')
    await screen.findByRole('heading', { name: /Shadow review/ })
    await user.click(screen.getByRole('button', { name: 'Reject, iterate' }))
    expect(await screen.findByRole('heading', { name: 'No staged version' })).toBeInTheDocument()

    cleanup()
    renderAt('/sources/manual_csv_upload/mappings')
    await screen.findByRole('heading', { name: /Mappings:/ })
    expect(screen.queryByText('STAGED')).not.toBeInTheDocument()
    expect(screen.getAllByText('ACTIVE')).toHaveLength(1)
  })

  it('renders the empty state for a source with no staged version', async () => {
    renderAt('/sources/src_unknown/shadow')
    expect(await screen.findByRole('heading', { name: 'No staged version' })).toBeInTheDocument()
  })

  it('shows the error state when the stats query errors', () => {
    vi.spyOn(shadow, 'useShadowStats').mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof shadow.useShadowStats>)
    renderAt('/sources/manual_csv_upload/shadow')
    expect(screen.getByRole('alert')).toHaveTextContent(/could not load the shadow rollout/i)
  })
})
