import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useReducer } from 'react'
import { describe, expect, it } from 'vitest'

import type { AtlasField, AtlasTable } from '../../lib/dis-ui-server/atlas'
import { RatifyGrid } from './RatifyGrid'
import { initialRatifyState, ratifyReducer } from './ratify-state'

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

const TABLE: AtlasTable = {
  key: 'store_sku_current_position',
  template_type: 'snapshot',
  semantics: 'merge_upsert',
  sink: 'canonical.store_sku_current_position',
  natural_key: [],
  fields: [
    field('store_id', { curated_bearing: true, origin: 'inferred', mandatory: true }),
    field('sku_id', { curated_bearing: true, origin: 'human', mandatory: true }),
    field('ingested_at', { produced_by: 'consumer_injected', origin: null }),
  ],
}

// A small harness so the grid runs on the real reducer (the route would own this useReducer).
function Harness({ readOnly = false }: { readOnly?: boolean }) {
  const [state, dispatch] = useReducer(ratifyReducer, initialRatifyState)
  return <RatifyGrid table={TABLE} state={state} dispatch={dispatch} readOnly={readOnly} />
}

function rowFor(name: string): HTMLElement {
  const row = document.querySelector(`[data-slot="ratify-row"][data-field="${name}"]`)
  if (row === null) {
    throw new Error(`no ratify row for ${name}`)
  }
  return row as HTMLElement
}

describe('RatifyGrid', () => {
  it('renders a row per field, grouped into inferred and system sections', () => {
    render(<Harness />)
    expect(screen.getByRole('heading', { name: 'Inferred fields' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'System fields (locked)' })).toBeInTheDocument()
    expect(rowFor('store_id')).toBeInTheDocument()
    expect(rowFor('sku_id')).toBeInTheDocument()
    expect(rowFor('ingested_at')).toBeInTheDocument()
  })

  it('reads origin and curated_bearing off the wire for the chips', () => {
    render(<Harness />)
    // store_id: inferred + curated-bearing -> Inferred badge + Needs ratification chip.
    expect(within(rowFor('store_id')).getByText('Inferred')).toBeInTheDocument()
    expect(within(rowFor('store_id')).getByText('Needs ratification')).toBeInTheDocument()
    // sku_id: already human -> Ratified.
    expect(within(rowFor('sku_id')).getByText('Ratified')).toBeInTheDocument()
    // the system field is locked and shown as System.
    expect(within(rowFor('ingested_at')).getByText('System')).toBeInTheDocument()
    expect(within(rowFor('ingested_at')).getByText(/Locked/i)).toBeInTheDocument()
  })

  it('an edit flips the origin marker inferred -> human', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const storeRow = rowFor('store_id')
    expect(within(storeRow).getByText('Inferred')).toBeInTheDocument()
    // Any attribute edit ratifies the field (ratify-on-edit).
    await user.click(within(storeRow).getByLabelText('Mandatory for store_id'))
    expect(within(rowFor('store_id')).getByText('Ratified')).toBeInTheDocument()
    expect(within(rowFor('store_id')).queryByText('Inferred')).not.toBeInTheDocument()
  })

  it('readOnly renders pure display: no edit controls anywhere', () => {
    render(<Harness readOnly />)
    // No checkboxes / text inputs in read-only mode (published is immutable).
    expect(document.querySelectorAll('input').length).toBe(0)
    expect(screen.getByText(/Published and immutable/i)).toBeInTheDocument()
    // The values still render as text.
    expect(within(rowFor('store_id')).getByText(/Mandatory: Yes/)).toBeInTheDocument()
  })
})
