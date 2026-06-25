import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { renderWithProviders } from '../../test/renderWithProviders'
import { DisUiServerHttpError } from '../../lib/dis-ui-server/client'
import type { AtlasDraft, AtlasField } from '../../lib/dis-ui-server/atlas'
import { RatifyConsole } from './RatifyConsole'

// Mock the NETWORK functions, and redefine useDraft to a real useQuery over the mocked getDraft
// using the SAME query key (draftQueryKey) the console reconciles against. This exercises the
// PATCH-then-publish reconciliation genuinely (queryClient.setQueryData on that key drives the
// grid), rather than stubbing useDraft to a static value. (Mocking the getDraft export alone is
// not enough: the real useDraft closes over the real getDraft, not the override.)
vi.mock('../../lib/dis-ui-server/atlas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/dis-ui-server/atlas')>()
  const { useQuery } = await import('@tanstack/react-query')
  const getDraft = vi.fn()
  return {
    ...actual,
    getDraft,
    patchDraft: vi.fn(),
    publishDraft: vi.fn(),
    useDraft: (draftId?: string) =>
      useQuery({
        queryKey: actual.draftQueryKey(draftId ?? ''),
        queryFn: () => getDraft(draftId),
        enabled: draftId !== undefined,
        retry: false,
      }),
  }
})

import { getDraft, patchDraft, publishDraft } from '../../lib/dis-ui-server/atlas'

const superAdmin: AuthSnapshot = {
  userId: 'u_superadmin01',
  tenantId: null,
  storeId: null,
  roles: ['atlas:schema:publish', 'dis:read'],
}

function field(name: string, extra: Partial<AtlasField> = {}): AtlasField {
  return {
    name,
    produced_by: 'mapping_produced',
    type_ref: 'text',
    nullable: false,
    default: null,
    mandatory: false,
    max_length: null,
    precision: null,
    scale: null,
    enum_ref: null,
    enum_candidate: [],
    pii: null,
    origin: 'inferred',
    display_name: null,
    description: null,
    provenance: null,
    curated_bearing: false,
    ...extra,
  }
}

function draft(fields: AtlasField[], naturalKey: string[]): AtlasDraft {
  return {
    draft_id: 'd1',
    vertical: 'retail',
    status: 'draft',
    schema_version: 1,
    system_profile: 'dis.v1',
    table: {
      key: 'store_sku_current_position',
      template_type: 'snapshot',
      semantics: 'merge_upsert',
      sink: 'canonical.store_sku_current_position',
      natural_key: naturalKey,
      fields,
    },
  }
}

function renderConsole() {
  return renderWithProviders(
    <Routes>
      <Route path="/atlas/drafts/:draftId" element={<RatifyConsole />} />
      <Route path="/atlas/drafts/:draftId/receipt" element={<p>receipt page</p>} />
    </Routes>,
    { snapshot: superAdmin, initialEntries: ['/atlas/drafts/d1'] },
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('RatifyConsole publish affordance (THE ONE DESIGN RULE)', () => {
  it('DISABLES publish while a curated-bearing field is still inferred', async () => {
    vi.mocked(getDraft).mockResolvedValue(
      draft(
        [field('store_id', { curated_bearing: true, origin: 'inferred', mandatory: true })],
        ['store_id'],
      ),
    )
    renderConsole()
    const button = await screen.findByRole('button', { name: 'Publish' })
    expect(button).toBeDisabled()
    expect(screen.getByText(/still need ratification/i)).toBeInTheDocument()
  })

  it('ENABLES publish once curated fields are human and the natural key is set', async () => {
    vi.mocked(getDraft).mockResolvedValue(
      draft(
        [
          field('store_id', { curated_bearing: true, origin: 'human', mandatory: true }),
          field('sku_id', { curated_bearing: true, origin: 'human', mandatory: true }),
        ],
        ['store_id', 'sku_id'],
      ),
    )
    renderConsole()
    const button = await screen.findByRole('button', { name: 'Publish' })
    expect(button).toBeEnabled()
  })

  it('NATURAL-KEY MEMBERS: a set natural_key with an inferred member keeps publish DISABLED', async () => {
    // The affordance-level mirror of the backend ratify_violations load-bearing case.
    vi.mocked(getDraft).mockResolvedValue(
      draft(
        [
          field('store_id', { curated_bearing: true, origin: 'inferred', mandatory: true }),
          field('sku_id', { curated_bearing: true, origin: 'human', mandatory: true }),
        ],
        ['store_id', 'sku_id'], // key is SET, but store_id (a member) is still inferred
      ),
    )
    renderConsole()
    const button = await screen.findByRole('button', { name: 'Publish' })
    expect(button).toBeDisabled()
  })

  it('PATCH-ok then publish-422: ratifications persist (not reverted) AND violations render', async () => {
    const initial = draft(
      [field('store_id', { curated_bearing: true, origin: 'inferred', mandatory: true })],
      ['store_id'],
    )
    // The PATCH response is the PERSISTED draft: store_id flipped to human server-side.
    const persisted = draft(
      [field('store_id', { curated_bearing: true, origin: 'human', mandatory: true })],
      ['store_id'],
    )
    vi.mocked(getDraft).mockResolvedValue(initial)
    vi.mocked(patchDraft).mockResolvedValue(persisted)
    vi.mocked(publishDraft).mockRejectedValue(
      new DisUiServerHttpError(422, 'draft_not_ratified', 'not ratified', {
        violations: [
          'store_sku_current_position.store_id: curated attribute still origin: inferred',
        ],
      }),
    )

    const user = userEvent.setup()
    renderConsole()

    // Ratify-on-edit locally enables the button (the convenience), then publish.
    const storeRow = await waitFor(() => {
      const row = document.querySelector('[data-slot="ratify-row"][data-field="store_id"]')
      if (row === null) throw new Error('no store_id row yet')
      return row as HTMLElement
    })
    await user.click(within(storeRow).getByLabelText('Mandatory for store_id'))
    await user.click(screen.getByRole('button', { name: 'Publish' }))

    // The PATCH ran, the cache reconciled to the persisted draft, then publish 422'd.
    await waitFor(() => expect(patchDraft).toHaveBeenCalledTimes(1))
    expect(publishDraft).toHaveBeenCalledTimes(1)

    // The 422 is authoritative: the violations render.
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /store_id: curated attribute still origin: inferred/,
    )

    // The user's ratification is NOT lost: store_id shows Ratified (persisted), never reverted to Inferred.
    const reconciledRow = document.querySelector(
      '[data-slot="ratify-row"][data-field="store_id"]',
    ) as HTMLElement
    expect(within(reconciledRow).getByText('Ratified')).toBeInTheDocument()
    expect(within(reconciledRow).queryByText('Inferred')).not.toBeInTheDocument()

    // A retry publishes against stored state (the button is enabled again, not stuck).
    expect(screen.getByRole('button', { name: 'Publish' })).toBeEnabled()
  })
})
