import { useState } from 'react'
import type { FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { SOURCE_TYPES } from '../lib/dis-ui-server/sources'

export type SourceFormValues = { source_id: string; name: string; type: string; store: string }

// Shared source form (redesign R4): the ONE form behind both SourceCreate and SourceEdit,
// collapsing the field clone the audit flagged. The only mode difference is source_id:
// create sets it once (editable, required); edit shows it READ-ONLY (immutable key, FM4).
// Name/Type/Store are identical in both. The screens own their mutation; this owns the
// fields, the create-mode source_id validation, and the submit/cancel buttons. No CRUD
// behavior changes from the extraction; the data layer (sources.ts) is untouched.
export function SourceForm({
  mode,
  initial,
  submitting,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  mode: 'create' | 'edit'
  initial: SourceFormValues
  submitting: boolean
  submitLabel: string
  onSubmit: (values: SourceFormValues) => void
  onCancel: () => void
}) {
  const [sourceId, setSourceId] = useState(initial.source_id)
  const [name, setName] = useState(initial.name)
  const [type, setType] = useState(initial.type)
  const [storeName, setStoreName] = useState(initial.store)
  const [error, setError] = useState<string | null>(null)

  function submit(event: FormEvent): void {
    event.preventDefault()
    // source_id is set once at create and required; on edit it is the fixed immutable key.
    if (mode === 'create' && sourceId.trim().length === 0) {
      setError('A source id is required.')
      return
    }
    setError(null)
    onSubmit({
      source_id: mode === 'create' ? sourceId.trim() : initial.source_id,
      name,
      type,
      store: storeName,
    })
  }

  return (
    <Card>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          {mode === 'create' ? (
            <div>
              <Label htmlFor="source-id">Source id</Label>
              <Input
                id="source-id"
                value={sourceId}
                onChange={(e) => setSourceId(e.target.value)}
                placeholder="e.g. manual_csv_upload"
                className="mt-1 font-mono"
              />
            </div>
          ) : (
            <div>
              <span className="text-label text-muted-foreground">Source id (immutable)</span>
              <p className="mt-1 font-mono text-sm">{initial.source_id}</p>
            </div>
          )}
          <div>
            <Label htmlFor="source-name">Name</Label>
            <Input id="source-name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
          </div>
          <div>
            <Label htmlFor="source-type">Type</Label>
            <Select id="source-type" value={type} onChange={(e) => setType(e.target.value)} className="mt-1">
              {SOURCE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="source-store">Store</Label>
            <Input id="source-store" value={storeName} onChange={(e) => setStoreName(e.target.value)} className="mt-1" />
          </div>

          {error !== null ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}

          <div className="flex gap-3">
            <Button type="submit" disabled={submitting}>
              {submitLabel}
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
