import { screen, within } from '@testing-library/react'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { AppRoutes } from './AppRoutes'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

describe('SourceTemplates (T2 templates list)', () => {
  it("lists the source's templates with active/staged/draft and versions count", async () => {
    renderWithProviders(<AppRoutes />, {
      snapshot: tenant,
      initialEntries: ['/sources/manual_csv_upload/templates'],
    })
    expect(await screen.findByRole('heading', { name: /Templates: manual_csv_upload/ })).toBeInTheDocument()
    // the two D68 templates (sales + inventory)
    expect(screen.getByText('Sales')).toBeInTheDocument()
    expect(screen.getByText('Inventory')).toBeInTheDocument()
    // active version badge present (Sales active v2)
    expect(screen.getByText('v2')).toBeInTheDocument()
  })

  it('links a template to its detail', async () => {
    renderWithProviders(<AppRoutes />, {
      snapshot: tenant,
      initialEntries: ['/sources/manual_csv_upload/templates'],
    })
    await screen.findByRole('heading', { name: /Templates: manual_csv_upload/ })
    const links = screen.getAllByRole('link', { name: 'View' })
    expect(links.length).toBeGreaterThan(0)
    expect(links[0].getAttribute('href')).toMatch(/^\/sources\/manual_csv_upload\/templates\//)
  })

  it('renders the empty state for a source with no templates', async () => {
    renderWithProviders(<AppRoutes />, {
      snapshot: tenant,
      initialEntries: ['/sources/no_such_source/templates'],
    })
    expect(await screen.findByRole('heading', { name: /No templates for this source/ })).toBeInTheDocument()
  })

  it('mounts under the dark theme class', async () => {
    const { container } = renderWithProviders(
      <div className="dark">
        <AppRoutes />
      </div>,
      { snapshot: tenant, initialEntries: ['/sources/manual_csv_upload/templates'] },
    )
    expect(await within(container).findByText('Sales')).toBeInTheDocument()
  })
})
