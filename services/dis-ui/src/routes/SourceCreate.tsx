import { useNavigate } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { SourceForm } from '../components/SourceForm'
import { makeSourceDraft, useCreateSource } from '../lib/dis-ui-server/sources'

// Sources create (slice 27), TENANT slice. Declares a new source via the shared SourceDraft
// (reconciled with the onboarding attach-to-new path). source_id is set once here (kind-style
// key, immutable thereafter). Adds to the mutable fixture; the Sources index reflects it.
// R4: the form fields are the shared SourceForm (dedup with SourceEdit); behavior unchanged.
export function SourceCreate() {
  const { snapshot } = useAuth()
  const navigate = useNavigate()
  const create = useCreateSource(snapshot)

  return (
    <section className="max-w-xl">
      <header className="mb-4">
        <h1 className="text-display">New source</h1>
        <p className="text-caption text-muted-foreground">Declare a data source for this tenant.</p>
      </header>

      <SourceForm
        mode="create"
        initial={makeSourceDraft({})}
        submitting={create.isPending}
        submitLabel="Create source"
        onSubmit={(values) =>
          create.mutate(makeSourceDraft(values), { onSuccess: () => navigate('/sources') })
        }
        onCancel={() => navigate('/sources')}
      />
    </section>
  )
}
