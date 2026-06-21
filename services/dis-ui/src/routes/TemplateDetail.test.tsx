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
// The Sales template fixture id (mapping-templates.ts).
const SALES = '/sources/manual_csv_upload/templates/0190ac10-5a00-7000-8a00-0000000000a1'

function renderDetail(path: string) {
  return renderWithProviders(<AppRoutes />, { snapshot: tenant, initialEntries: [path] })
}

describe('TemplateDetail (T2: lineage + field/rules split + store context)', () => {
  it('renders the version lineage and makes the active version prominent', async () => {
    renderDetail(SALES)
    expect(await screen.findByRole('heading', { name: 'Sales' })).toBeInTheDocument()
    expect(screen.getByText('Active: v2')).toBeInTheDocument()
    expect(screen.getByText('Version history')).toBeInTheDocument()
    // lineage shows the three statuses
    expect(screen.getByText('active')).toBeInTheDocument()
    expect(screen.getByText('staged')).toBeInTheDocument()
    expect(screen.getByText('deprecated')).toBeInTheDocument()
  })

  it('presents the active mapping as FIELD mappings (catalog-enriched) and FORMAT rules', async () => {
    renderDetail(SALES)
    await screen.findByRole('heading', { name: 'Sales' })
    // The type-aware catalog is keyed on the template's template_type, so it resolves just
    // after the detail; wait for the enrichment (the catalog section renders only once a
    // field is resolved) before asserting the enriched rows.
    await screen.findAllByText('sale_event')
    // field mappings: a source column maps to a catalog field; the catalog enrichment
    // (canonical key + section) renders; store_id is never a target (FM3)
    expect(screen.getByText('Field mappings')).toBeInTheDocument()
    expect(screen.getByText('item_code')).toBeInTheDocument()
    expect(screen.getByText('sku_id')).toBeInTheDocument()
    expect(screen.getAllByText('sale_event').length).toBeGreaterThan(0) // catalog section
    expect(screen.queryByText('store_id')).not.toBeInTheDocument()
    // format rules: the real normalize shape renders a date format (polars string) + a
    // decimal separator
    expect(screen.getByText('Format rules')).toBeInTheDocument()
    expect(screen.getByText(/format=%d-%m-%Y/)).toBeInTheDocument()
    expect(screen.getByText(/decimal_separator/)).toBeInTheDocument()
  })

  it('shows store locale context (currency / timezone / tax treatment)', async () => {
    renderDetail(SALES)
    await screen.findByRole('heading', { name: 'Sales' })
    expect(screen.getByText('Store context')).toBeInTheDocument()
    // two stores share USD/exclusive; the timezone is store-unique
    expect(screen.getAllByText('USD').length).toBeGreaterThan(0)
    expect(screen.getByText('America/New_York')).toBeInTheDocument()
    expect(screen.getAllByText('exclusive').length).toBeGreaterThan(0)
  })

  it('renders an error state for an unknown template (404 contract)', async () => {
    renderDetail('/sources/manual_csv_upload/templates/no-such-template')
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load this template/i)
  })

  it('mounts under the dark theme class', async () => {
    const { container } = renderWithProviders(
      <div className="dark">
        <AppRoutes />
      </div>,
      { snapshot: tenant, initialEntries: [SALES] },
    )
    expect(await within(container).findByRole('heading', { name: 'Sales' })).toBeInTheDocument()
  })
})
