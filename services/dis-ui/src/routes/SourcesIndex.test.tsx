import { screen } from '@testing-library/react'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import * as sources from '../lib/dis-ui-server/sources'
import { __resetSourcesFixture } from '../lib/dis-ui-server/sources'
import { renderWithProviders } from '../test/renderWithProviders'
import { SourcesIndex } from './SourcesIndex'

const acmeSnapshot: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}

// A tenant with no source fixtures -> empty state.
const otherTenantSnapshot: AuthSnapshot = {
  userId: 'u_other0001',
  tenantId: 't_nofixtures01',
  storeId: null,
  roles: ['dis:read'],
}

describe('SourcesIndex', () => {
  beforeEach(() => {
    __resetSourcesFixture()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('lists the tenant fixture sources', async () => {
    renderWithProviders(<SourcesIndex />, { snapshot: acmeSnapshot })
    expect(await screen.findByRole('heading', { name: 'Manage sources' })).toBeInTheDocument()
    expect(screen.getByText('Manual CSV Upload')).toBeInTheDocument()
    // T2: the Manage-sources "Mappings" affordance now leads to the templates list.
    expect(screen.getByRole('link', { name: 'Mappings' })).toHaveAttribute(
      'href',
      '/sources/manual_csv_upload/templates',
    )
  })

  it('renders the source-type identity per row (R1 identity helper)', async () => {
    const { container } = renderWithProviders(<SourcesIndex />, { snapshot: acmeSnapshot })
    await screen.findByRole('heading', { name: 'Manage sources' })
    // the CSV source (type 'CSV') carries the csv identity, applied via the helper's literal
    // Tailwind classes (so this proves the single helper drives the row identity).
    expect(container.querySelector('.text-source-csv')).not.toBeNull()
  })

  it('shows the empty state for a tenant with no sources', async () => {
    renderWithProviders(<SourcesIndex />, { snapshot: otherTenantSnapshot })
    expect(await screen.findByRole('heading', { name: 'No sources' })).toBeInTheDocument()
  })

  it('shows the loading state on first paint', () => {
    renderWithProviders(<SourcesIndex />, { snapshot: acmeSnapshot })
    expect(screen.getByRole('status')).toHaveTextContent(/loading sources/i)
  })

  it('shows the error state when the sources query errors', () => {
    vi.spyOn(sources, 'useSources').mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof sources.useSources>)
    renderWithProviders(<SourcesIndex />, { snapshot: acmeSnapshot })
    expect(screen.getByRole('alert')).toHaveTextContent(/could not load sources/i)
  })

  it('offers Create and per-row Edit, with deprecate moved out (T6) and no hard-delete control', async () => {
    renderWithProviders(<SourcesIndex />, { snapshot: acmeSnapshot })
    await screen.findByRole('heading', { name: 'Manage sources' })
    // Connect a System is the add-source front door (the old /connect picker was retired)
    expect(screen.getByRole('link', { name: 'New source' })).toHaveAttribute(
      'href',
      '/connectors/new',
    )
    expect(screen.getByRole('link', { name: 'Edit' })).toHaveAttribute(
      'href',
      '/sources/manual_csv_upload/edit',
    )
    // T6: Deprecate is no longer here; it lives in SourceEdit (the per-source "Manage source").
    expect(screen.queryByRole('button', { name: 'Deprecate' })).not.toBeInTheDocument()
    // FM1: there is no hard-delete control anywhere
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /delete/i })).not.toBeInTheDocument()
  })
})
