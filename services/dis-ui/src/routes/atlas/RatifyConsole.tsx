import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useReducer } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { ErrorState } from '../../components/states/ErrorState'
import { LoadingState } from '../../components/states/LoadingState'
import { DisUiServerHttpError } from '../../lib/dis-ui-server/client'
import { draftQueryKey, patchDraft, publishDraft, useDraft } from '../../lib/dis-ui-server/atlas'
import type { AtlasDraft } from '../../lib/dis-ui-server/atlas'
import { RatifyGrid } from './RatifyGrid'
import {
  canPublish,
  hasPendingEdits,
  initialRatifyState,
  naturalKeyUnsatisfied,
  ratifyReducer,
  remainingToRatify,
  toDraftPatch,
} from './ratify-state'

// The Atlas ratify console (A4 PR3b): edit + ratify a draft IR, then publish. THE ONE DESIGN
// RULE lives in the publish affordance here: the button disables on canPublish (a CONVENIENCE
// reading curated_bearing/origin off the wire), and the server 422 (draft_not_ratified, with
// details.violations) is AUTHORITATIVE, surfaced on the enabled-then-422 path.

type PublishError = { message: string; violations: string[] }

// Pull the violations off a 422 draft_not_ratified envelope (details.violations: string[]). Any
// other failure degrades to a generic message with no violations list.
function describePublishError(err: unknown): PublishError {
  if (err instanceof DisUiServerHttpError) {
    if (err.status === 422 && err.code === 'draft_not_ratified') {
      const raw = err.details.violations
      const violations = Array.isArray(raw)
        ? raw.filter((v): v is string => typeof v === 'string')
        : []
      return {
        message: 'The server rejected this publish: the draft is not fully ratified.',
        violations,
      }
    }
    return { message: err.message, violations: [] }
  }
  return { message: 'Publish failed. Please retry.', violations: [] }
}

export function RatifyConsole() {
  const { draftId } = useParams<{ draftId: string }>()
  const query = useDraft(draftId)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [state, dispatch] = useReducer(ratifyReducer, initialRatifyState)
  const [publishing, setPublishing] = useState(false)
  const [publishError, setPublishError] = useState<PublishError | null>(null)

  if (query.isPending) {
    return <LoadingState label="Loading draft schema..." />
  }
  if (query.isError || query.data === undefined) {
    return <ErrorState message="Could not load this draft." onRetry={() => void query.refetch()} />
  }

  const draft: AtlasDraft = query.data
  const table = draft.table
  const remaining = remainingToRatify(state, table)
  const keyUnsatisfied = naturalKeyUnsatisfied(state, table)
  const publishable = canPublish(state, table)
  const published = draft.status === 'published'

  async function handlePublish(): Promise<void> {
    if (draftId === undefined) {
      return
    }
    setPublishError(null)
    setPublishing(true)
    try {
      // 1. Persist the edits FIRST. The PATCH response is the updated draft (origins flipped to
      //    human, natural_key set). Reconcile the cache to it and clear the local edits BEFORE
      //    publishing, so if publish then 422s or the network drops, the grid reflects the
      //    now-PERSISTED ratifications (never a reverted state) and a retry publishes against
      //    stored state, not stale pre-click edits.
      if (hasPendingEdits(state)) {
        const updated = await patchDraft(draftId, toDraftPatch(state))
        queryClient.setQueryData(draftQueryKey(draftId), updated)
        dispatch({ type: 'reset' })
      }
      // 2. Publish. The server re-runs the ratify gate; a still-unratified draft 422s.
      const receipt = await publishDraft(draftId)
      navigate(`/atlas/drafts/${draftId}/receipt`, { state: receipt })
    } catch (err) {
      setPublishError(describePublishError(err))
    } finally {
      setPublishing(false)
    }
  }

  return (
    <section className="mx-auto flex w-full max-w-[920px] flex-col gap-8">
      <header className="flex flex-col gap-2">
        <Link to="/atlas" className="text-caption text-muted-foreground hover:text-foreground">
          Back to registry
        </Link>
        <h1 className="text-display">Ratify schema</h1>
        <p className="text-body text-muted-foreground">
          <span className="font-mono">{draft.vertical}</span> ·{' '}
          <span className="font-mono">{table.key}</span> · draft v{draft.schema_version}
        </p>
      </header>

      {published ? (
        <ErrorState message="This draft is already published and immutable." />
      ) : (
        <>
          <RatifyGrid table={table} state={state} dispatch={dispatch} />

          <div className="flex flex-col gap-3 border-t border-border pt-6">
            {/* The convenience summary: what the client can already see is unratified. */}
            {remaining.length > 0 ? (
              <p className="text-caption text-warning">
                {remaining.length} curated field{remaining.length === 1 ? '' : 's'} still need
                ratification.
              </p>
            ) : null}
            {keyUnsatisfied ? (
              <p className="text-caption text-warning">
                This merge_upsert table needs a natural key before it can publish.
              </p>
            ) : null}

            {/* The 422-authoritative path: the server rejected an enabled publish; show why. */}
            {publishError !== null ? (
              <div
                role="alert"
                className="flex flex-col gap-1 rounded-md border border-danger/40 bg-danger/5 p-3"
              >
                <p className="text-body-strong text-danger">{publishError.message}</p>
                {publishError.violations.length > 0 ? (
                  <ul className="list-disc pl-5 text-caption text-danger">
                    {publishError.violations.map((v) => (
                      <li key={v}>{v}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            <div className="flex gap-3">
              <Button
                type="button"
                disabled={publishing || !publishable}
                onClick={() => void handlePublish()}
              >
                {publishing ? 'Publishing...' : 'Publish'}
              </Button>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
