import { FileText, Loader2, UploadCloud, X } from 'lucide-react'
import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { formatBytes } from '../lib/format-bytes'

// Shared CSV dropzone with upload feedback. ONE component so every caller behaves the same
// (FM1): the Connect a System CSV branch (CsvUploadStep) and the batch ingest (RecurringBatchUpload).
//
// States:
// - EMPTY: the dashed prompt (drag-and-drop or browse). Real drag-and-drop is wired here
//   (onDragOver/onDrop), so the "Drag and drop" text is honest, not browse-only.
// - SELECTED: a file card (name + human size + optional row count) with Remove + Replace.
// - BUSY (in-flight): a role="status" spinner row with the caller's busyLabel. The LABEL is the
//   caller's responsibility so it stays accurate (FM3): "Analyzing ..." for the client-side
//   parse, "Uploading ..." for the real network upload. NO fake progress bar (FM2) - fetch does
//   not expose byte progress, so a spinner is the honest signal. Success/error are caller-owned.
//
// fileInputLabel defaults to "CSV file" so getByLabelText('CSV file') resolves the input
// regardless of the visible field label.
type FileDropzoneProps = {
  id: string
  label: string
  file: File | null
  onSelect: (file: File | null) => void
  accept?: string
  hint?: string
  rowCount?: number | null
  busy?: boolean
  busyLabel?: string
  disabled?: boolean
  fileInputLabel?: string
}

export function FileDropzone({
  id,
  label,
  file,
  onSelect,
  accept,
  hint,
  rowCount = null,
  busy = false,
  busyLabel,
  disabled = false,
  fileInputLabel = 'CSV file',
}: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)
  const locked = busy || disabled

  function pickFromDrop(dropped: FileList | null): void {
    const next = dropped?.[0] ?? null
    if (next !== null) {
      onSelect(next)
    }
  }

  function remove(): void {
    onSelect(null)
    // Reset so re-selecting the SAME file still fires onChange.
    if (inputRef.current !== null) {
      inputRef.current.value = ''
    }
  }

  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      {/* The input is always rendered (hidden) so Replace and the empty-state prompt share it. */}
      <input
        ref={inputRef}
        id={id}
        type="file"
        accept={accept}
        aria-label={fileInputLabel}
        disabled={locked}
        onChange={(e) => onSelect(e.target.files?.[0] ?? null)}
        className="sr-only"
      />

      {file === null ? (
        <label
          htmlFor={id}
          onDragOver={(e) => {
            if (locked) return
            e.preventDefault()
            setDragActive(true)
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={(e) => {
            if (locked) return
            e.preventDefault()
            setDragActive(false)
            pickFromDrop(e.dataTransfer.files)
          }}
          className={`mt-1 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed px-4 py-8 text-center transition-colors ${
            dragActive
              ? 'border-ring bg-muted'
              : 'border-border-strong bg-surface-raised/50 hover:bg-muted'
          }`}
        >
          <UploadCloud aria-hidden="true" className="h-6 w-6 text-muted-foreground" />
          <span className="text-body-strong">Drag and drop or browse</span>
          {hint !== undefined ? (
            <span className="text-caption text-muted-foreground">{hint}</span>
          ) : null}
        </label>
      ) : (
        <div
          onDragOver={(e) => {
            if (locked) return
            e.preventDefault()
            setDragActive(true)
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={(e) => {
            if (locked) return
            e.preventDefault()
            setDragActive(false)
            pickFromDrop(e.dataTransfer.files)
          }}
          className={`mt-1 flex flex-col gap-2 rounded-md border px-3 py-3 transition-colors ${
            dragActive ? 'border-ring bg-muted' : 'border-border bg-surface-raised/50'
          }`}
        >
          <div className="flex items-center gap-3">
            <FileText aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <p className="break-all text-body-strong">{file.name}</p>
              <p className="text-caption text-muted-foreground">
                {formatBytes(file.size)}
                {rowCount !== null ? ` · ${rowCount} rows` : ''}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={locked}
                onClick={() => inputRef.current?.click()}
              >
                Replace
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label="Remove file"
                disabled={locked}
                onClick={remove}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
          </div>

          {busy && busyLabel !== undefined ? (
            <div
              role="status"
              className="flex items-center gap-2 text-caption text-muted-foreground"
            >
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              {busyLabel}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
