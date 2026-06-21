import { screen, within } from '@testing-library/react'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppLayout } from './AppLayout'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

describe('AppLayout shell', () => {
  it('renders the top bar (theme toggle, logout) and the sidebar', async () => {
    renderWithProviders(<AppLayout />, { snapshot: tenant })
    expect(await screen.findByRole('button', { name: 'Toggle theme' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /log out/i })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument()
  })

  it('shows exactly one DIS brand, in the sidebar, not duplicated in the top bar (T8)', async () => {
    renderWithProviders(<AppLayout />, { snapshot: tenant })
    // The brand lives in the sidebar (Primary nav), with the DIS wordmark + the swappable mark.
    const sidebar = await screen.findByRole('navigation', { name: 'Primary' })
    expect(within(sidebar).getByText('DIS')).toBeInTheDocument()
    expect(within(sidebar).getByTestId('brand-mark')).toBeInTheDocument()
    // The top bar (banner) no longer carries its own DIS brand.
    expect(within(screen.getByRole('banner')).queryByText('DIS')).not.toBeInTheDocument()
    // Only one DIS brand on screen.
    expect(screen.getAllByText('DIS')).toHaveLength(1)
  })
})
