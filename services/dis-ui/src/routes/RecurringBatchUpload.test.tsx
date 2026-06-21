import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { renderWithProviders } from '../test/renderWithProviders'
import { DisUiServerHttpError } from '../lib/dis-ui-server/client'
import type { CsvUploadResult } from '../lib/dis-ui-server/csv-uploads'
import { uploadCsvWithSessionToken } from '../lib/dis-ui-server/csv-uploads'
import { AppRoutes } from './AppRoutes'

// The real upload module is mocked here: the request-shape fidelity is proven in
// csv-uploads.test.ts (mocked fetch). This file drives the SCREEN: store picker, honest copy,
// gating, success and error rendering.
vi.mock('../lib/dis-ui-server/csv-uploads', () => ({
  uploadCsvWithSessionToken: vi.fn(),
}))
const mockUpload = vi.mocked(uploadCsvWithSessionToken)

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:upload', 'dis:read'],
}
// mapping-templates.ts fixtures.
const SALES = '0190ac10-5a00-7000-8a00-0000000000a1' // active v2 (file source)
const PRICING = '0190ac10-5a00-7000-8a00-0000000000a3' // draft only, no active version (file)
const ORDERS = '0190ac10-5a00-7000-8a00-0000000000b1' // square_pos, API source (active)
const TEMPLATES = '/sources/manual_csv_upload/templates'

const RESULT: CsvUploadResult = {
  trace_id: '0190ac30-7c00-7000-8c00-0000000000d1',
  upload_id: 'us_abc123def456',
  tenant_id: 't_acme9k2l1mn4',
  store_id: '0190ac20-6b00-7000-8b00-0000000000c1',
  store_code: 'TX-102',
  source_id: 'manual_csv_upload',
  template_id: SALES,
  gcs_uri: 'gs://dis-bronze/tenant/x/source/manual_csv_upload/yyyy=2026/mm=06/dd=05/x.csv',
  row_count: 42,
  received_ts: '2026-06-05T10:00:00Z',
  status: 'received',
}

function render(path: string, dark = false) {
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
      initialEntries: [path],
    },
  )
}

beforeEach(() => {
  mockUpload.mockReset()
})
afterEach(() => {
  vi.clearAllMocks()
})

describe('Ingest data (real csv-uploads wiring)', () => {
  // active-version gating on the surfaces
  it('offers "Ingest data" on the template detail, enabled for an active template', async () => {
    render(`${TEMPLATES}/${SALES}`)
    await screen.findByRole('heading', { name: 'Sales' })
    expect(screen.getByRole('link', { name: 'Ingest data' })).toBeInTheDocument()
  })

  it('disables "Ingest data" with a reason when the template has no active version', async () => {
    render(`${TEMPLATES}/${PRICING}`)
    await screen.findByRole('heading', { name: 'Pricing' })
    expect(screen.getByRole('button', { name: 'Ingest data' })).toBeDisabled()
    expect(screen.getByText(/No active version yet/)).toBeInTheDocument()
  })

  it('shows the action per-template in the templates list, gated by active version', async () => {
    render(TEMPLATES)
    await screen.findByRole('heading', { name: /Templates: manual_csv_upload/ })
    expect(screen.getAllByRole('link', { name: 'Ingest data' }).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByRole('button', { name: 'Ingest data' })).toBeDisabled()
  })

  // honest copy + store picker + read-only context
  it('shows a store picker, the active mapping as context, and honest copy (no version-N claim)', async () => {
    render(`${TEMPLATES}/${SALES}/upload`)
    await screen.findByRole('heading', { name: 'Upload Data' })
    // store picker from stores-onboarded (only the ACTIVE coded store is offered)
    const store = screen.getByLabelText('Store')
    expect(
      within(store).getByRole('option', { name: /Acme Downtown #1 \(TX-102\)/ }),
    ).toBeInTheDocument()
    // honest framing: "uploaded against [template]" + async "active mapping version" copy.
    expect(screen.getByText(/uploaded against/i)).toBeInTheDocument()
    expect(screen.getByText(/ingested asynchronously/i)).toBeInTheDocument()
    expect(screen.getByText(/active mapping version/i)).toBeInTheDocument()
    // no over-claim: never "will use ... v2", and no claim a specific version was pinned.
    expect(screen.queryByText(/will use .*v2/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/pinned/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/mapping_version_id/i)).not.toBeInTheDocument()
    // active mapping shown as read-only context (reused display), no editable review
    expect(screen.getByText('Field mappings')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Canonical for/)).not.toBeInTheDocument()
  })

  // confirm -> real call with the contract args; honest success copy
  it('uploads the file against the template for the chosen store, then shows an honest result', async () => {
    mockUpload.mockResolvedValueOnce(RESULT)
    const user = userEvent.setup()
    render(`${TEMPLATES}/${SALES}/upload`)
    await screen.findByRole('heading', { name: 'Upload Data' })

    await user.selectOptions(screen.getByLabelText('Store'), 'TX-102')
    await user.upload(
      screen.getByLabelText('CSV file'),
      new File(['a,b\n1,2\n'], 'batch.csv', { type: 'text/csv' }),
    )
    await user.click(screen.getByRole('button', { name: /upload and ingest/i }))

    expect(mockUpload).toHaveBeenCalledTimes(1)
    const arg = mockUpload.mock.calls[0][0]
    expect(arg.templateId).toBe(SALES)
    expect(arg.storeCode).toBe('TX-102')
    expect(arg.file).toBeInstanceOf(File)

    const status = await screen.findByText(/Uploaded batch\.csv/)
    // filename-led success copy, shown only on a real 2xx.
    expect(status).toHaveTextContent(/42 rows received against Sales/)
    // async framing kept; no over-claim of a pinned/specific applied version.
    expect(status).toHaveTextContent(/ingested\s+asynchronously/i)
    expect(status).toHaveTextContent(/active mapping version/i)
    expect(status).not.toHaveTextContent(/version-pinned/i)
    expect(status).not.toHaveTextContent(/mapping_version_id/i)
  })

  // on-drop file card: selecting a file replaces the empty prompt with the file card (name).
  it('shows the file card with the filename after a file is selected', async () => {
    const user = userEvent.setup()
    render(`${TEMPLATES}/${SALES}/upload`)
    await screen.findByRole('heading', { name: 'Upload Data' })
    expect(screen.getByText('Drag and drop or browse')).toBeInTheDocument()
    await user.upload(
      screen.getByLabelText('CSV file'),
      new File(['a,b\n1,2\n'], 'batch.csv', { type: 'text/csv' }),
    )
    expect(screen.getByText('batch.csv')).toBeInTheDocument()
    expect(screen.queryByText('Drag and drop or browse')).not.toBeInTheDocument()
  })

  // in-flight: a pending upload shows the role=status "Uploading {filename}..." spinner state,
  // then resolves to the filename-led success copy. NO fake progress (spinner only, FM2).
  it('shows an "Uploading..." in-flight state while the upload is pending', async () => {
    let resolveUpload: (v: CsvUploadResult) => void = () => {}
    mockUpload.mockReturnValueOnce(
      new Promise<CsvUploadResult>((resolve) => {
        resolveUpload = resolve
      }),
    )
    const user = userEvent.setup()
    render(`${TEMPLATES}/${SALES}/upload`)
    await screen.findByRole('heading', { name: 'Upload Data' })
    await user.selectOptions(screen.getByLabelText('Store'), 'TX-102')
    await user.upload(
      screen.getByLabelText('CSV file'),
      new File(['a,b\n1,2\n'], 'batch.csv', { type: 'text/csv' }),
    )
    await user.click(screen.getByRole('button', { name: /upload and ingest/i }))
    // in-flight: accurate "Uploading" label (not "Analyzing"), announced via role=status.
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Uploading batch.csv...')
    expect(status).not.toHaveTextContent(/Analyzing/i)
    // resolving settles to the success state.
    resolveUpload(RESULT)
    expect(await screen.findByText(/Uploaded batch\.csv/)).toBeInTheDocument()
  })

  it('surfaces a server 409 store-not-active as a clear error', async () => {
    mockUpload.mockRejectedValueOnce(
      new DisUiServerHttpError(409, 'store_state_conflict', 'not active', {}),
    )
    const user = userEvent.setup()
    render(`${TEMPLATES}/${SALES}/upload`)
    await screen.findByRole('heading', { name: 'Upload Data' })
    await user.selectOptions(screen.getByLabelText('Store'), 'TX-102')
    await user.upload(
      screen.getByLabelText('CSV file'),
      new File(['a,b\n1,2\n'], 'batch.csv', { type: 'text/csv' }),
    )
    await user.click(screen.getByRole('button', { name: /upload and ingest/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/store is not active/i)
  })

  it('guards direct navigation when the template has no active version', async () => {
    render(`${TEMPLATES}/${PRICING}/upload`)
    await screen.findByRole('heading', { name: 'Upload Data' })
    expect(screen.getByText(/No active version yet/)).toBeInTheDocument()
    expect(screen.queryByLabelText('CSV file')).not.toBeInTheDocument()
  })

  // T8: API/connector sources sync automatically; the upload route is guarded (defense in
  // depth) so direct navigation cannot reach the dropzone, even though the affordance is
  // already removed from the Ingest Data list.
  it('guards direct navigation for an API source (no upload form, shows connected/syncing)', async () => {
    render(`/sources/square_pos/templates/${ORDERS}/upload`)
    await screen.findByRole('heading', { name: 'Orders' })
    expect(screen.getByText('Connected / syncing')).toBeInTheDocument()
    expect(screen.getByText(/syncs automatically/i)).toBeInTheDocument()
    // no upload affordance at all
    expect(screen.queryByLabelText('CSV file')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /upload and ingest/i })).not.toBeInTheDocument()
  })

  it('mounts under the dark theme class', async () => {
    const { container } = render(`${TEMPLATES}/${SALES}/upload`, true)
    expect(
      await within(container).findByRole('heading', { name: 'Upload Data' }),
    ).toBeInTheDocument()
  })
})
