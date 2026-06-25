import { Link, useParams } from 'react-router'

import { ErrorState } from '../../components/states/ErrorState'
import { LoadingState } from '../../components/states/LoadingState'
import { StatusBadge } from '../../components/StatusBadge'
import { useDraft } from '../../lib/dis-ui-server/atlas'
import { RatifyGrid } from './RatifyGrid'
import { initialRatifyState } from './ratify-state'

// Published-schema detail (A4 PR3b): the frozen IR rendered through the RatifyGrid in readOnly
// mode, so it is pure display, no edit controls and no publish button. "Published is immutable"
// is visible here, not only enforced by the PR2 trigger. Read-only needs no reducer, so the grid
// gets the initial (empty) state and no dispatch.
export function PublishedDetail() {
  const { draftId } = useParams<{ draftId: string }>()
  const query = useDraft(draftId)

  if (query.isPending) {
    return <LoadingState label="Loading published schema..." />
  }
  if (query.isError || query.data === undefined) {
    return <ErrorState message="Could not load this schema." onRetry={() => void query.refetch()} />
  }

  const draft = query.data
  return (
    <section className="mx-auto flex w-full max-w-[920px] flex-col gap-8">
      <header className="flex flex-col gap-2">
        <Link to="/atlas" className="text-caption text-muted-foreground hover:text-foreground">
          Back to registry
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-display">Published schema</h1>
          <StatusBadge tone="success">{draft.status}</StatusBadge>
        </div>
        <p className="text-body text-muted-foreground">
          <span className="font-mono">{draft.vertical}</span> ·{' '}
          <span className="font-mono">{draft.table.key}</span> · v{draft.schema_version}
        </p>
      </header>

      <RatifyGrid table={draft.table} state={initialRatifyState} readOnly />
    </section>
  )
}
