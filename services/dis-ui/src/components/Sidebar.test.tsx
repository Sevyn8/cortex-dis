import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { Sidebar } from './Sidebar'
import type { NavItem } from './nav'

const tenantSnapshot: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}
const opsSnapshot: AuthSnapshot = {
  userId: 'u_opsdev0001',
  tenantId: null,
  storeId: null,
  roles: ['dis:ops', 'dis:read'],
}

// Inject an ops-flagged item so the gate is exercised without depending on a real ops nav item
// (none remain in NAV_ITEMS after Ops Fleet was removed). T7: items carry a section.
const ITEMS: NavItem[] = [
  { label: 'Sources', to: '/sources', section: 'DATA' },
  { label: 'Ops Tool', to: '/ops/tool', ops: true, section: 'MONITORING' },
]

describe('Sidebar nav gating', () => {
  it('always renders tenant items', () => {
    renderWithProviders(<Sidebar items={ITEMS} />, { snapshot: tenantSnapshot })
    expect(screen.getByRole('link', { name: 'Sources' })).toBeInTheDocument()
  })

  it('hides ops items from a tenant snapshot', () => {
    renderWithProviders(<Sidebar items={ITEMS} />, { snapshot: tenantSnapshot })
    expect(screen.queryByRole('link', { name: 'Ops Tool' })).not.toBeInTheDocument()
  })

  it('shows ops items for an ops snapshot', () => {
    renderWithProviders(<Sidebar items={ITEMS} />, { snapshot: opsSnapshot })
    expect(screen.getByRole('link', { name: 'Ops Tool' })).toBeInTheDocument()
  })

  it('marks the active route', () => {
    renderWithProviders(<Sidebar items={ITEMS} />, {
      snapshot: tenantSnapshot,
      initialEntries: ['/sources'],
    })
    // Selector-only update for slice 23 chrome: the active marker moved from
    // `font-semibold` to the sidebar-accent chrome. The asserted behavior (the active
    // route is visually marked) is unchanged.
    expect(screen.getByRole('link', { name: 'Sources' }).className).toContain('bg-sidebar-accent')
  })

  it('no longer renders the removed Ops Fleet nav item, for ops or tenant (default NAV_ITEMS)', () => {
    const tenantView = renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    expect(screen.queryByRole('link', { name: 'Ops Fleet' })).not.toBeInTheDocument()
    tenantView.unmount()
    renderWithProviders(<Sidebar />, { snapshot: opsSnapshot })
    // Ops Fleet was removed; with no ops-only items left, the OPERATIONS section is gone too.
    expect(screen.queryByRole('link', { name: 'Ops Fleet' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'OPERATIONS' })).not.toBeInTheDocument()
  })

  it('no longer renders the retired Fleet Quarantine / Fleet Audit items (T9), even for ops', () => {
    // T9 merged the fleet views into the scope-aware Quarantine / Audit; the separate Fleet
    // items are gone for everyone.
    const tenantView = renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    expect(screen.queryByRole('link', { name: 'Fleet Quarantine' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Fleet Audit' })).not.toBeInTheDocument()
    tenantView.unmount()
    renderWithProviders(<Sidebar />, { snapshot: opsSnapshot })
    expect(screen.queryByRole('link', { name: 'Fleet Quarantine' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Fleet Audit' })).not.toBeInTheDocument()
    // one scope-aware Quarantine + one Audit remain (MONITORING), for ops too
    expect(screen.getByRole('link', { name: 'Quarantine' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Audit' })).toBeInTheDocument()
  })

  it('no longer renders an OPERATIONS section for ops (it had no items left after Ops Fleet)', () => {
    const { container } = renderWithProviders(<Sidebar />, { snapshot: opsSnapshot })
    const order = Array.from(container.querySelectorAll('h2, a')).map((el) => el.textContent)
    expect(order).not.toContain('OPERATIONS')
  })

  it('no longer renders the removed Query ops item, for ops or tenant', () => {
    const tenantView = renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    expect(screen.queryByRole('link', { name: 'Query' })).not.toBeInTheDocument()
    tenantView.unmount()
    renderWithProviders(<Sidebar />, { snapshot: opsSnapshot })
    expect(screen.queryByRole('link', { name: 'Query' })).not.toBeInTheDocument()
  })

  it('renders the tenant DATA nav label "Upload Data" and not the retired labels', () => {
    renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    expect(screen.getByRole('link', { name: 'Upload Data' })).toBeInTheDocument()
    // "Add Source" was retired with the old connector picker (/connect) and the /upload wizard;
    // "Connect a System" is the single add-source nav door now.
    expect(screen.queryByRole('link', { name: 'Add Source' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Connect a System' })).toBeInTheDocument()
    // The prior "Upload CSV" label is renamed, and "New CSV Template" is removed as a nav door.
    expect(screen.queryByRole('link', { name: 'Upload CSV' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'New CSV Template' })).not.toBeInTheDocument()
    // Older retired labels stay gone.
    expect(screen.queryByRole('link', { name: 'Ingest Data' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Create Template' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Sources' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Manage Sources' })).not.toBeInTheDocument()
  })

  it('renders "Connect a System" immediately below "Upload Data" (Add Source retired)', () => {
    renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    const connect = screen.getByRole('link', { name: 'Connect a System' })
    expect(connect).toHaveAttribute('href', '/connectors/new')
    // ordered immediately below Upload Data (Add Source removed from between them)
    const links = screen.getAllByRole('link')
    const uploadIdx = links.findIndex((l) => l.textContent === 'Upload Data')
    const connectIdx = links.findIndex((l) => l.textContent === 'Connect a System')
    expect(uploadIdx).toBeGreaterThanOrEqual(0)
    expect(connectIdx).toBe(uploadIdx + 1)
  })

  it('renders the brand header (DIS wordmark + DATA PLATFORM subtitle + placeholder mark)', () => {
    renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    expect(screen.getByText('DIS')).toBeInTheDocument()
    expect(screen.getByText('DATA PLATFORM')).toBeInTheDocument()
    // the placeholder mark is isolated (BrandMark); it carries the swap-point markers (T8: DIS)
    const mark = screen.getByTestId('brand-mark')
    expect(mark).toHaveAttribute('data-placeholder', 'dis-logo')
  })

  it('groups the tenant nav into OVERVIEW / DATA / MONITORING sections (no OPERATIONS)', () => {
    renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    expect(screen.getByRole('heading', { name: 'OVERVIEW' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'DATA' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'MONITORING' })).toBeInTheDocument()
    // OPERATIONS was removed entirely (its last item, Ops Fleet, is gone), so it never renders.
    expect(screen.queryByRole('heading', { name: 'OPERATIONS' })).not.toBeInTheDocument()
  })

  it('places each item under the right section header (DOM order)', () => {
    const { container } = renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    // The DATA header is immediately followed by its items, then MONITORING. After retiring the
    // old Add Source picker, DATA is Upload Data -> Connect a System.
    const order = Array.from(container.querySelectorAll('h2, a')).map((el) => el.textContent)
    const dataIdx = order.indexOf('DATA')
    const monIdx = order.indexOf('MONITORING')
    expect(dataIdx).toBeGreaterThanOrEqual(0)
    expect(monIdx).toBeGreaterThan(dataIdx)
    expect(order.slice(dataIdx + 1, monIdx)).toEqual(['Upload Data', 'Connect a System'])
    // OVERVIEW (Dashboard) precedes DATA.
    expect(order.indexOf('OVERVIEW')).toBeLessThan(dataIdx)
  })

  it('renders thin-stroke icons on nav items', () => {
    const { container } = renderWithProviders(<Sidebar />, { snapshot: tenantSnapshot })
    // thin icons: strokeWidth 1.5 (lucide default is 2)
    const thin = container.querySelectorAll('svg[stroke-width="1.5"]')
    expect(thin.length).toBeGreaterThan(0)
  })

  it('mounts under the dark theme class', () => {
    const { container } = renderWithProviders(
      <div className="dark">
        <Sidebar />
      </div>,
      { snapshot: tenantSnapshot },
    )
    expect(within(container).getByText('DIS')).toBeInTheDocument()
    expect(within(container).getByRole('heading', { name: 'DATA' })).toBeInTheDocument()
  })

  it('collapses and expands', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Sidebar items={ITEMS} />, { snapshot: tenantSnapshot })
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument()
  })
})
