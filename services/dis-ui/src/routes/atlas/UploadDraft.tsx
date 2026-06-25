import { useState } from 'react'
import { useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { FileDropzone } from '../../components/FileDropzone'
import { createDraft } from '../../lib/dis-ui-server/atlas'
import { DisUiServerHttpError } from '../../lib/dis-ui-server/client'

// Upload -> draft (A4 PR3b), following CsvUploadStep + FileDropzone. The Super Admin picks a
// vertical, optionally a table key, and one or more example CSVs; createDraft profiles + proposes
// + assembles a draft IR (the A3 path, server-side) and returns it. We then navigate to the
// ratify console for the new draft. The full upload->infer->ratify->publish loop is the manual
// run-local walkthrough; this is its entry point.
export function UploadDraft() {
  const navigate = useNavigate()
  const [vertical, setVertical] = useState('')
  const [tableKey, setTableKey] = useState('')
  // Multiple example files (the BFF accepts a files[] list). FileDropzone is single-file, so we
  // accumulate selections into a list and show them; selecting again appends.
  const [files, setFiles] = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSubmit = vertical.trim().length > 0 && files.length > 0 && !submitting

  function addFile(file: File | null): void {
    if (file === null) {
      return
    }
    // De-dupe by name so re-selecting the same file does not double it.
    setFiles((prev) => [...prev.filter((f) => f.name !== file.name), file])
  }

  function removeFile(name: string): void {
    setFiles((prev) => prev.filter((f) => f.name !== name))
  }

  async function handleSubmit(): Promise<void> {
    setError(null)
    setSubmitting(true)
    try {
      const draft = await createDraft(
        vertical.trim(),
        files,
        tableKey.trim().length > 0 ? tableKey.trim() : undefined,
      )
      navigate(`/atlas/drafts/${draft.draft_id}`)
    } catch (err) {
      setError(
        err instanceof DisUiServerHttpError
          ? err.message
          : 'Could not create a draft from these files. Check the files and retry.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="mx-auto flex w-full max-w-[640px] flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-display">New schema draft</h1>
        <p className="text-body text-muted-foreground">
          Upload example exports for a vertical. We infer a draft canonical schema you then ratify.
        </p>
      </header>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="atlas-vertical">Vertical</Label>
        <Input
          id="atlas-vertical"
          placeholder="pharma"
          value={vertical}
          onChange={(e) => setVertical(e.target.value)}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="atlas-table-key">Table key (optional)</Label>
        <Input
          id="atlas-table-key"
          placeholder="store_sku_current_position"
          value={tableKey}
          onChange={(e) => setTableKey(e.target.value)}
        />
      </div>

      <FileDropzone
        id="atlas-examples"
        label="Example CSVs"
        accept=".csv,text/csv"
        hint="Upload one or more sample exports. We read the columns to infer the schema."
        file={null}
        onSelect={addFile}
      />

      {files.length > 0 ? (
        <ul className="flex flex-col gap-1.5">
          {files.map((f) => (
            <li
              key={f.name}
              className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-caption"
            >
              <span className="font-mono">{f.name}</span>
              <Button type="button" variant="ghost" size="sm" onClick={() => removeFile(f.name)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : null}

      {error !== null ? (
        <div
          role="alert"
          className="rounded-md border border-danger/40 bg-danger/5 p-3 text-caption text-danger"
        >
          {error}
        </div>
      ) : null}

      <div className="flex gap-3">
        <Button type="button" disabled={!canSubmit} onClick={() => void handleSubmit()}>
          {submitting ? 'Creating draft...' : 'Create draft'}
        </Button>
      </div>
    </section>
  )
}
