import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { FileDropzone } from './FileDropzone'

function csv(name = 'sample.csv'): File {
  return new File(['a,b\n1,2\n'], name, { type: 'text/csv' })
}

// A controlled harness so onSelect drives the rendered `file` (the real caller pattern).
function Harness({
  busy = false,
  busyLabel,
  rowCount = null,
  onSelectSpy,
}: {
  busy?: boolean
  busyLabel?: string
  rowCount?: number | null
  onSelectSpy?: (f: File | null) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  return (
    <FileDropzone
      id="csv-file"
      label="Batch CSV file"
      file={file}
      onSelect={(f) => {
        onSelectSpy?.(f)
        setFile(f)
      }}
      accept=".csv"
      hint="CSV up to 10 MB"
      rowCount={rowCount}
      busy={busy}
      busyLabel={busyLabel}
    />
  )
}

describe('FileDropzone', () => {
  it('renders the empty prompt and resolves the input by "CSV file" (label-agnostic)', () => {
    render(<Harness />)
    expect(screen.getByText('Drag and drop or browse')).toBeInTheDocument()
    expect(screen.getByText('CSV up to 10 MB')).toBeInTheDocument()
    // fileInputLabel defaults to "CSV file" even though the visible label is "Batch CSV file".
    expect(screen.getByLabelText('CSV file')).toBeInTheDocument()
  })

  it('shows a file card with name and human-readable size after selecting', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.upload(screen.getByLabelText('CSV file'), csv('batch.csv'))
    expect(screen.getByText('batch.csv')).toBeInTheDocument()
    // 8 bytes of content ("a,b\n1,2\n") -> "8 B".
    expect(screen.getByText(/8 B/)).toBeInTheDocument()
    // the empty prompt is gone
    expect(screen.queryByText('Drag and drop or browse')).not.toBeInTheDocument()
  })

  it('shows the row count when provided', async () => {
    const user = userEvent.setup()
    render(<Harness rowCount={42} />)
    await user.upload(screen.getByLabelText('CSV file'), csv('batch.csv'))
    expect(screen.getByText(/42 rows/)).toBeInTheDocument()
  })

  it('Remove clears the selection (onSelect(null)) and returns to the empty prompt', async () => {
    const user = userEvent.setup()
    const onSelectSpy = vi.fn()
    render(<Harness onSelectSpy={onSelectSpy} />)
    await user.upload(screen.getByLabelText('CSV file'), csv('batch.csv'))
    await user.click(screen.getByRole('button', { name: 'Remove file' }))
    expect(onSelectSpy).toHaveBeenLastCalledWith(null)
    expect(screen.getByText('Drag and drop or browse')).toBeInTheDocument()
  })

  it('Replace opens the file picker (clicks the hidden input)', async () => {
    const user = userEvent.setup()
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    render(<Harness />)
    await user.upload(screen.getByLabelText('CSV file'), csv('batch.csv'))
    clickSpy.mockClear()
    await user.click(screen.getByRole('button', { name: 'Replace' }))
    expect(clickSpy).toHaveBeenCalledTimes(1)
    clickSpy.mockRestore()
  })

  it('accepts a real drag-and-drop (onDrop -> onSelect(first file))', () => {
    const onSelectSpy = vi.fn()
    render(<Harness onSelectSpy={onSelectSpy} />)
    const dropped = csv('dragged.csv')
    const zone = screen.getByText('Drag and drop or browse').closest('label') as HTMLLabelElement
    fireEvent.drop(zone, { dataTransfer: { files: [dropped] } })
    expect(onSelectSpy).toHaveBeenCalledWith(dropped)
  })

  it('shows a role=status spinner with the busy label and disables Remove/Replace in-flight', async () => {
    const user = userEvent.setup()
    function BusyHarness() {
      const [file, setFile] = useState<File | null>(null)
      return (
        <FileDropzone
          id="csv-file"
          label="Batch CSV file"
          file={file}
          onSelect={setFile}
          busy={file !== null}
          busyLabel={file !== null ? `Uploading ${file.name}...` : undefined}
        />
      )
    }
    render(<BusyHarness />)
    await user.upload(screen.getByLabelText('CSV file'), csv('batch.csv'))
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Uploading batch.csv...')
    expect(screen.getByRole('button', { name: 'Replace' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove file' })).toBeDisabled()
  })
})
