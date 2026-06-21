import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import * as notifications from '../lib/dis-ui-server/notifications'
import { __resetNotificationsFixture } from '../lib/dis-ui-server/notifications'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}
const emptyTenant: AuthSnapshot = { ...tenant, tenantId: 't_nofixtures01' }

// Rendered through AppRoutes at /notifications so the header bell and the screen
// share one QueryClient: a mutation on the screen must move the bell's count.
function renderAt(snapshot: AuthSnapshot) {
  return renderWithProviders(<AppRoutes />, { snapshot, initialEntries: ['/notifications'] })
}

describe('Notifications (tenant slice)', () => {
  beforeEach(() => {
    __resetNotificationsFixture()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the list with severity and read state, and the bell shows the unread count', async () => {
    renderAt(tenant)
    expect(await screen.findByRole('heading', { name: 'Notifications' })).toBeInTheDocument()
    expect(await screen.findByText('2 rows quarantined')).toBeInTheDocument()
    // severity now renders as a semantic badge (word, not bracketed text) - selector-only
    expect(screen.getByText('warning')).toBeInTheDocument()
    expect(screen.getByText('error')).toBeInTheDocument()
    // the already-read seed row shows a "read" marker rather than a button
    expect(screen.getByText('Source connected')).toBeInTheDocument()
    expect(screen.getAllByText('read').length).toBe(1)
    // bell (distinct accessible name from the sidebar nav link)
    expect(screen.getByRole('link', { name: 'Notifications, 3 unread' })).toBeInTheDocument()
  })

  it('narrows the list when a filter is selected', async () => {
    const user = userEvent.setup()
    renderAt(tenant)
    await screen.findByRole('heading', { name: 'Notifications' })
    await user.click(screen.getByRole('button', { name: 'Errors' }))
    expect(await screen.findByText(/Canonical-shape validation failed/)).toBeInTheDocument()
    expect(screen.queryByText('2 rows quarantined')).not.toBeInTheDocument()
    expect(screen.queryByText('Source connected')).not.toBeInTheDocument()
  })

  it('mark-all-read zeroes the bell and marks every row read', async () => {
    const user = userEvent.setup()
    renderAt(tenant)
    await screen.findByRole('heading', { name: 'Notifications' })
    await user.click(screen.getByRole('button', { name: 'Mark all read' }))
    expect(await screen.findByRole('link', { name: 'Notifications, 0 unread' })).toBeInTheDocument()
  })

  it('mark-read on a single row lowers the bell count', async () => {
    const user = userEvent.setup()
    renderAt(tenant)
    await screen.findByRole('heading', { name: 'Notifications' })
    const row = (await screen.findByText('2 rows quarantined')).closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Mark read' }))
    expect(await screen.findByRole('link', { name: 'Notifications, 2 unread' })).toBeInTheDocument()
  })

  it('shows the empty state for a tenant with no notifications', async () => {
    renderAt(emptyTenant)
    expect(await screen.findByRole('heading', { name: 'No notifications' })).toBeInTheDocument()
  })

  it('shows the error state when the list query errors', () => {
    vi.spyOn(notifications, 'useNotifications').mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof notifications.useNotifications>)
    renderAt(tenant)
    expect(screen.getByRole('alert')).toHaveTextContent(/could not load notifications/i)
  })
})
