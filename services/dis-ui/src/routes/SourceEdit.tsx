import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { SourceForm } from '../components/SourceForm'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { useDeprecateSource, useSource, useUpdateSource } from '../lib/dis-ui-server/sources'
import type { Source } from '../lib/dis-ui-server/sources'

// Sources edit (slice 27 / T6), TENANT slice. Edits the display metadata (name / type / store).
// source_id is the immutable key (FM4): shown READ-ONLY, never re-keyed. Updates the mutable
// fixture; the index reflects it. R4: the fields are the shared SourceForm (dedup with
// SourceCreate). T6: this is now the single per-source "Manage source" home, reached from
// Ingest Data, so the Deprecate action (soft active -> deprecated, no hard delete) lives here
// rather than on the sources list. The deprecate logic (useDeprecateSource) is unchanged.
export function SourceEdit() {
  const { sourceId } = useParams()
  const { snapshot } = useAuth()
  const detail = useSource(snapshot, sourceId ?? null)

  if (sourceId === undefined) {
    return <EmptyState title="No source" message="No source id in the URL." />
  }
  if (detail.isPending) {
    return <LoadingState label="Loading source..." />
  }
  if (detail.isError) {
    return <ErrorState message="Could not load the source." onRetry={() => void detail.refetch()} />
  }
  if (detail.data === null) {
    return <EmptyState title="Source not found" message={`No source ${sourceId} for this tenant.`} />
  }

  // Remount the form per source so its fields initialize from the loaded source (the shared
  // SourceForm lazy-inits from `initial`).
  return <SourceEditForm key={detail.data.source_id} source={detail.data} />
}

function SourceEditForm({ source }: { source: Source }) {
  const { snapshot } = useAuth()
  const navigate = useNavigate()
  const update = useUpdateSource(snapshot, source.source_id)
  const deprecate = useDeprecateSource(snapshot)
  const [deprecating, setDeprecating] = useState(false)

  return (
    <section className="max-w-xl">
      <header className="mb-4">
        <h1 className="text-display">Edit source</h1>
        <p className="text-caption text-muted-foreground">Update this source's display metadata.</p>
      </header>

      <SourceForm
        mode="edit"
        initial={{ source_id: source.source_id, name: source.name, type: source.type, store: source.store }}
        submitting={update.isPending}
        submitLabel="Save changes"
        onSubmit={(values) =>
          update.mutate(
            { name: values.name, type: values.type, store: values.store },
            { onSuccess: () => navigate('/sources') },
          )
        }
        onCancel={() => navigate('/sources')}
      />

      {/* Deprecate (T6): the source-manage action now lives once per source, here. Soft
          active -> deprecated, retained history, no hard delete (FM1). Hidden once already
          deprecated, matching the prior list behavior. */}
      {source.status !== 'deprecated' ? (
        <div className="mt-6 border-t border-border pt-4">
          <p className="text-caption text-muted-foreground">
            Stop this source from accepting new submissions. Its history is retained.
          </p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2"
            onClick={() => setDeprecating(true)}
          >
            Deprecate source
          </Button>
        </div>
      ) : null}

      {/* Soft deprecate confirm (no hard delete, FM1). */}
      <Dialog open={deprecating} onOpenChange={setDeprecating}>
        <DialogTrigger render={<span className="sr-only" />} />
        <DialogContent showCloseButton={false}>
          <DialogTitle>Deprecate this source?</DialogTitle>
          <p className="text-sm text-muted-foreground">
            The source moves to deprecated and stops accepting new submissions. Its history is
            retained; this is not a delete.
          </p>
          <div className="flex justify-end gap-3">
            <Button type="button" variant="ghost" onClick={() => setDeprecating(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={deprecate.isPending}
              onClick={() =>
                deprecate.mutate(source.source_id, {
                  onSuccess: () => {
                    setDeprecating(false)
                    navigate('/sources')
                  },
                })
              }
            >
              Confirm deprecate
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  )
}
