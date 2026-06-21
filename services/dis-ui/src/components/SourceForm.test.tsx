import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { SourceForm } from './SourceForm'
import type { SourceFormValues } from './SourceForm'

const EMPTY: SourceFormValues = { source_id: '', name: '', type: 'CSV', store: '' }

// The ONE shared form behind SourceCreate and SourceEdit (R4, FM2). Create mode exposes an
// editable source_id and requires it; edit mode shows source_id read-only (immutable, FM4).
describe('SourceForm (shared by create + edit)', () => {
  it('create mode: editable source_id + fields, submits the typed values', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(
      <SourceForm
        mode="create"
        initial={EMPTY}
        submitting={false}
        submitLabel="Create source"
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.getByLabelText('Source id')).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
    expect(screen.getByLabelText('Type')).toBeInTheDocument()
    expect(screen.getByLabelText('Store')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Source id'), 'square_pos')
    await user.type(screen.getByLabelText('Name'), 'Square POS')
    await user.click(screen.getByRole('button', { name: 'Create source' }))
    expect(onSubmit).toHaveBeenCalledWith({ source_id: 'square_pos', name: 'Square POS', type: 'CSV', store: '' })
  })

  it('create mode: requires a source id (alert, no submit)', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(
      <SourceForm
        mode="create"
        initial={EMPTY}
        submitting={false}
        submitLabel="Create source"
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Create source' }))
    expect(screen.getByRole('alert')).toHaveTextContent(/source id is required/i)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('edit mode: source_id is read-only (no editable input), submits metadata only', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(
      <SourceForm
        mode="edit"
        initial={{ source_id: 'manual_csv_upload', name: 'Manual CSV Upload', type: 'CSV', store: 'Acme Downtown #1' }}
        submitting={false}
        submitLabel="Save changes"
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    )
    // immutable key shown, but NOT an editable field
    expect(screen.getByText('manual_csv_upload')).toBeInTheDocument()
    expect(screen.queryByLabelText('Source id')).not.toBeInTheDocument()

    const nameInput = screen.getByLabelText('Name')
    await user.clear(nameInput)
    await user.type(nameInput, 'Renamed CSV')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(onSubmit).toHaveBeenCalledWith({
      source_id: 'manual_csv_upload',
      name: 'Renamed CSV',
      type: 'CSV',
      store: 'Acme Downtown #1',
    })
  })
})
