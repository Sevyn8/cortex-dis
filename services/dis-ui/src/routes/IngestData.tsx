import { Link } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import type { MappingTemplate } from '../lib/dis-ui-server/mapping-templates'
import { useMappingTemplates } from '../lib/dis-ui-server/mapping-templates'
import { useTemplateTypes } from '../lib/dis-ui-server/template-types'

// Upload Data (the template registry), at /ingest. A FLAT list of per-template cards: each card
// is a reusable mapping a batch can be ingested against. The friendly template-type label is
// sourced VERBATIM from GET /template-types (useTemplateTypes display_name) - never hardcoded -
// and degrades to no badge when a template has no template_type (legacy/missing) or its key is
// unresolved. Data source + actions are unchanged: useMappingTemplates (GET /mapping-templates),
// View mapping -> TemplateDetail, Ingest data -> RecurringBatchUpload (gated on an active
// version; API/connector sources sync automatically and show a status instead). "Manage source"
// stays per card so the SourceEdit (edit + deprecate) entry point is preserved.

export function IngestData() {
  const { snapshot } = useAuth()
  // null source filter -> all templates across sources.
  const list = useMappingTemplates(snapshot, null)
  // Friendly template-type labels: key -> display_name, served by GET /template-types. The label
  // shown is whatever the endpoint returns for the key (verbatim); we never author our own.
  const types = useTemplateTypes()
  const typeLabels = new Map<string, string>()
  for (const t of types.data ?? []) {
    typeLabels.set(t.key, t.display_name)
  }
  // The friendly label for a template, or null when absent/unresolved (degrade: no badge).
  function templateTypeLabel(template: MappingTemplate): string | null {
    if (template.template_type === undefined) {
      return null
    }
    return typeLabels.get(template.template_type) ?? null
  }

  const header = (
    <header className="flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h1 className="text-display">Upload Data</h1>
        <p className="text-caption text-muted-foreground">
          Every mapping template across your sources. Pick one to upload a new batch of data.
        </p>
      </div>
    </header>
  )

  if (list.isPending) {
    return <LoadingState label="Loading templates..." />
  }
  if (list.isError) {
    return <ErrorState message="Could not load templates." onRetry={() => void list.refetch()} />
  }
  if (list.data.length === 0) {
    return (
      <section className="flex flex-col gap-6">
        {header}
        <EmptyState
          title="No templates yet"
          message="Create a template first, then come back to ingest data against it."
        />
      </section>
    )
  }

  return (
    <section className="flex flex-col gap-6">
      {header}

      <div className="flex flex-col gap-3">
        {list.data.map((template) => {
          const typeLabel = templateTypeLabel(template)
          const hasVersion =
            template.active_version !== null ||
            template.staged_version !== null ||
            template.draft_version !== null
          return (
            <Card key={template.template_id} className="p-5">
              <CardContent className="flex flex-col gap-4 p-0">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-subheading text-foreground">
                        {template.template_name}
                      </span>
                      {/* Friendly template-type label (verbatim from /template-types). Omitted
                          when the template has no template_type or its key is unresolved. */}
                      {typeLabel !== null ? (
                        <StatusBadge tone="neutral">{typeLabel}</StatusBadge>
                      ) : null}
                    </div>
                    <div className="text-caption text-muted-foreground">
                      Source: <span className="font-mono">{template.source_id}</span> ·{' '}
                      {template.versions_count} version{template.versions_count === 1 ? '' : 's'}
                    </div>
                  </div>

                  {/* Lifecycle status badges (current tones). */}
                  <div className="flex flex-wrap items-center gap-2">
                    {template.active_version !== null ? (
                      <StatusBadge tone="success">Active v{template.active_version}</StatusBadge>
                    ) : null}
                    {template.staged_version !== null ? (
                      <StatusBadge tone="warning">Staged v{template.staged_version}</StatusBadge>
                    ) : null}
                    {template.draft_version !== null ? (
                      <StatusBadge tone="neutral">Draft v{template.draft_version}</StatusBadge>
                    ) : null}
                    {!hasVersion ? <StatusBadge tone="neutral">No versions</StatusBadge> : null}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    to={`/sources/${template.source_id}/templates/${template.template_id}`}
                    className={buttonVariants({ variant: 'outline', size: 'sm' })}
                  >
                    View mapping
                  </Link>
                  {/* Ingest affordance by ingestion mode (T8), preserved: API/connector sources
                      sync automatically (a status, no manual upload); file/CSV sources keep the
                      gated ingest action (enabled only with an active version; the server also
                      rejects a non-active template with 409). */}
                  {template.ingestion_mode === 'api' ? (
                    <StatusBadge tone="info">Connected / syncing</StatusBadge>
                  ) : template.active_version !== null ? (
                    <Link
                      to={`/sources/${template.source_id}/templates/${template.template_id}/upload`}
                      className={buttonVariants({ variant: 'default', size: 'sm' })}
                    >
                      Ingest data
                    </Link>
                  ) : (
                    <button
                      type="button"
                      disabled
                      title="No active version to ingest against yet"
                      className={buttonVariants({ variant: 'default', size: 'sm' })}
                    >
                      Ingest data
                    </button>
                  )}
                  {/* Per-card source-management entry point (edit + deprecate, in SourceEdit). */}
                  <Link
                    to={`/sources/${template.source_id}/edit`}
                    className={buttonVariants({ variant: 'ghost', size: 'sm' })}
                  >
                    Manage source
                  </Link>
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </section>
  )
}
