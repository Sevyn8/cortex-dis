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

function render(dark = false) {
  return renderWithProviders(
    dark ? (
      <div className="dark">
        <AppRoutes />
      </div>
    ) : (
      <AppRoutes />
    ),
    {
      snapshot: tenant,
      initialEntries: ['/ingest'],
    },
  )
}

// Upload Data: a FLAT list of per-template cards (no source_id grouping). Each card shows the
// template name, a friendly template-type label (sourced verbatim from GET /template-types),
// source + version count, lifecycle status, and the actions (View mapping, Ingest data gated on
// an active version, per-card Manage source). API sources show "Connected / syncing".
describe('IngestData (flat per-template registry)', () => {
  it('renders the "Upload Data" registry with one card per template', async () => {
    render()
    await screen.findByRole('heading', { name: 'Upload Data' })
    // the four fixture templates render (Sales/Inventory/Pricing on manual_csv_upload, Orders on
    // square_pos). 'Orders'/'Inventory'/'Pricing' are unique text; 'Sales' collides with its own
    // friendly type badge so it is not asserted via getByText here.
    expect(screen.getByText('Orders')).toBeInTheDocument()
    expect(screen.getByText('Inventory')).toBeInTheDocument()
    expect(screen.getByText('Pricing')).toBeInTheDocument()
  })

  it('shows the source + version meta per card (no source group headings)', async () => {
    render()
    await screen.findByRole('heading', { name: 'Upload Data' })
    // source is a per-card meta field now, not a group heading: manual_csv_upload carries three
    // templates, square_pos one.
    expect(screen.getAllByText('manual_csv_upload')).toHaveLength(3)
    expect(screen.getByText('square_pos')).toBeInTheDocument()
    // the old source-group headings are gone.
    expect(screen.queryByRole('heading', { name: 'manual_csv_upload' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'square_pos' })).not.toBeInTheDocument()
  })

  it('surfaces the friendly template_type label from /template-types, and degrades when absent', async () => {
    render()
    await screen.findByRole('heading', { name: 'Upload Data' })
    // The Inventory template (template_type 'inventory_change') shows the endpoint's display_name
    // verbatim - proving the label is sourced from useTemplateTypes, not hardcoded here.
    expect(screen.getByText('Inventory change')).toBeInTheDocument()
    // Graceful degrade: the Pricing template has NO template_type in the fixture; the page still
    // renders it and no 'snapshot' label leaks in (no fixture template uses that key).
    expect(screen.getByText('Pricing')).toBeInTheDocument()
    expect(screen.queryByText('Catalogue snapshot')).not.toBeInTheDocument()
  })

  it('links each card to the template detail via "View mapping"', async () => {
    render()
    await screen.findByRole('heading', { name: 'Upload Data' })
    const views = screen.getAllByRole('link', { name: 'View mapping' })
    expect(views).toHaveLength(4) // one per template
    expect(views[0].getAttribute('href')).toMatch(/^\/sources\/[^/]+\/templates\//)
  })

  it('offers a per-template ingest action for FILE sources, gated by active version', async () => {
    render()
    await screen.findByRole('heading', { name: 'Upload Data' })
    // file source: Sales + Inventory active -> enabled links; Pricing (no active) -> disabled
    // button. Orders is API, so it has no ingest action.
    expect(screen.getAllByRole('link', { name: 'Ingest data' })).toHaveLength(2)
    const disabled = screen.getByRole('button', { name: 'Ingest data' })
    expect(disabled).toBeDisabled()
    expect(disabled.getAttribute('title')).toMatch(/no active version/i)
  })

  it('shows "Connected / syncing" for an API source instead of an ingest action', async () => {
    render()
    await screen.findByRole('heading', { name: 'Upload Data' })
    expect(screen.getByText('Connected / syncing')).toBeInTheDocument()
    // only the two active FILE templates (Sales, Inventory) link to the recurring-batch upload;
    // the API (square_pos / Orders) template offers no such link.
    const uploadLinks = screen
      .getAllByRole('link')
      .map((l) => l.getAttribute('href'))
      .filter(
        (href): href is string =>
          href !== null && href.includes('/templates/') && href.endsWith('/upload'),
      )
    expect(uploadLinks).toHaveLength(2)
    expect(uploadLinks.some((href) => href.includes('square_pos'))).toBe(false)
  })

  it('keeps a per-card "Manage source" link into SourceEdit for every template', async () => {
    render()
    await screen.findByRole('heading', { name: 'Upload Data' })
    const manage = screen.getAllByRole('link', { name: 'Manage source' })
    expect(manage).toHaveLength(4) // one per template card
    const hrefs = manage.map((link) => link.getAttribute('href'))
    expect(hrefs).toContain('/sources/manual_csv_upload/edit')
    expect(hrefs).toContain('/sources/square_pos/edit')
  })

  it('mounts under the dark theme class', async () => {
    const { container } = render(true)
    expect(
      await within(container).findByRole('heading', { name: 'Upload Data' }),
    ).toBeInTheDocument()
  })
})
