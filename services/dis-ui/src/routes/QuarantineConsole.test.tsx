import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import * as quarantineApi from '../lib/dis-ui-server/quarantine-api'
import { renderWithProviders } from '../test/renderWithProviders'
import { QuarantineConsole } from './QuarantineConsole'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}

// Fixture trace ids (quarantine-api.ts): two manual_csv_upload rows + one shopify_pos_v2 chunk.
const TRACE_CANONICAL = '0190ac0e-1a01-7001-8a01-000000000001' // manual, canonical-shape, v1
const TRACE_NORMALIZE = '0190ac0e-1a01-7001-8a01-000000000002' // manual, normalization
const TRACE_SHOPIFY = '0190ac0e-1a01-7001-8a01-000000000003' // shopify chunk, source-shape, no version

describe('QuarantineConsole (tenant slice, real endpoints)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the held items and the filter-independent open-count badge', async () => {
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    expect(await screen.findByRole('button', { name: TRACE_CANONICAL })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: TRACE_SHOPIFY })).toBeInTheDocument()
    expect(screen.getAllByText('manual_csv_upload').length).toBeGreaterThan(0)
    expect(screen.getByText('3 open')).toBeInTheDocument()
  })

  it('narrows the list by source (server-side); open count stays filter-independent', async () => {
    const user = userEvent.setup()
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    await screen.findByRole('button', { name: TRACE_CANONICAL })
    await user.selectOptions(screen.getByLabelText('Source filter'), 'shopify_pos_v2')
    expect(await screen.findByRole('button', { name: TRACE_SHOPIFY })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRACE_CANONICAL })).not.toBeInTheDocument()
    // open_count is independent of the active filters
    expect(screen.getByText('3 open')).toBeInTheDocument()
  })

  it('pre-applies the source filter from the ?source= param (Dashboard deep link)', async () => {
    renderWithProviders(<QuarantineConsole />, {
      snapshot: tenant,
      initialEntries: ['/?source=shopify_pos_v2'],
    })
    expect(await screen.findByRole('button', { name: TRACE_SHOPIFY })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRACE_CANONICAL })).not.toBeInTheDocument()
  })

  it('narrows by error type (server-side)', async () => {
    const user = userEvent.setup()
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    await screen.findByRole('button', { name: TRACE_CANONICAL })
    await user.selectOptions(screen.getByLabelText('Error type filter'), 'source-shape')
    expect(await screen.findByRole('button', { name: TRACE_SHOPIFY })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRACE_CANONICAL })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRACE_NORMALIZE })).not.toBeInTheDocument()
  })

  it('status "resolved" yields nothing today (no resolve path)', async () => {
    const user = userEvent.setup()
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    await screen.findByRole('button', { name: TRACE_CANONICAL })
    await user.selectOptions(screen.getByLabelText('Status filter'), 'resolved')
    expect(await screen.findByRole('heading', { name: 'No matching rows' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRACE_CANONICAL })).not.toBeInTheDocument()
  })

  it('opens row detail (mapping version + context) and handles the always-null payload honestly', async () => {
    const user = userEvent.setup()
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    await user.click(await screen.findByRole('button', { name: TRACE_CANONICAL }))
    expect(await screen.findByRole('heading', { name: 'Row detail' })).toBeInTheDocument()
    // assert detail-unique content (error_reason also appears in the list cell)
    expect(
      screen.getByText(/canonical-shape: column price failed numeric cast/),
    ).toBeInTheDocument()
    expect(screen.getByText(/· v1/)).toBeInTheDocument() // mapping_version 1
    // original_payload is always null this slice: an honest note, never a broken "null" block
    expect(screen.getByText(/Original payload is not available yet/i)).toBeInTheDocument()
  })

  it('a chunk detail with no mapping version renders without a version token', async () => {
    const user = userEvent.setup()
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    await user.click(await screen.findByRole('button', { name: TRACE_SHOPIFY }))
    expect(await screen.findByRole('heading', { name: 'Row detail' })).toBeInTheDocument()
    expect(screen.getByText(/source-shape: required column sku absent/)).toBeInTheDocument()
    expect(screen.getByText(/Original payload is not available yet/i)).toBeInTheDocument()
    // mapping_version is null for a pre-lookup chunk failure: no version token rendered
    expect(screen.queryByText(/· v/)).not.toBeInTheDocument()
  })

  it('renders NO resubmit, resolve, or dismiss action (none exists server-side)', async () => {
    const user = userEvent.setup()
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    await user.click(await screen.findByRole('button', { name: TRACE_CANONICAL }))
    await screen.findByRole('heading', { name: 'Row detail' })
    expect(screen.queryByRole('button', { name: /resubmit/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /resolve/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /dismiss/i })).not.toBeInTheDocument()
  })

  it('shows the empty state when the tenant has no held items', () => {
    vi.spyOn(quarantineApi, 'useQuarantineList').mockReturnValue({
      data: { items: [], open_count: 0 },
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof quarantineApi.useQuarantineList>)
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    expect(screen.getByRole('heading', { name: 'No quarantined rows' })).toBeInTheDocument()
  })

  it('shows the error state when the list query errors', () => {
    vi.spyOn(quarantineApi, 'useQuarantineList').mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof quarantineApi.useQuarantineList>)
    renderWithProviders(<QuarantineConsole />, { snapshot: tenant })
    expect(screen.getByRole('alert')).toHaveTextContent(/could not load quarantined rows/i)
  })
})
