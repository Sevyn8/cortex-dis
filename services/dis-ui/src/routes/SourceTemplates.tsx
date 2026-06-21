import { Link, useParams } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import { useMappingTemplates } from '../lib/dis-ui-server/mapping-templates'

// Source templates (T2, template grain D68), at /sources/:sourceId/templates. A source
// carries N named mapping templates (sales, inventory, pricing); this lists them with the
// active/staged/draft version state. A template leads to its detail (version lineage +
// the active version's mapping). Template-keyed (template_id is the addressing key;
// source_id groups). Read-only here; authoring/lifecycle is a later slice.
export function SourceTemplates() {
  const { sourceId } = useParams()
  const { snapshot } = useAuth()
  const list = useMappingTemplates(snapshot, sourceId ?? null)

  if (sourceId === undefined) {
    return <EmptyState title="No source" message="No source id in the URL." />
  }
  if (list.isPending) {
    return <LoadingState label="Loading templates..." />
  }
  if (list.isError) {
    return <ErrorState message="Could not load templates." onRetry={() => void list.refetch()} />
  }
  if (list.data.length === 0) {
    return (
      <EmptyState title="No templates for this source" message={`No mapping templates for ${sourceId}.`} />
    )
  }

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h1 className="text-display">Templates: {sourceId}</h1>
        <p className="text-caption text-muted-foreground">
          Each template is a reusable mapping for one shape of batch from this source.
        </p>
      </header>

      <Card>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Template</TableHead>
                <TableHead>Active</TableHead>
                <TableHead>Staged</TableHead>
                <TableHead>Draft</TableHead>
                <TableHead>Versions</TableHead>
                <TableHead>Last updated</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.data.map((template) => (
                <TableRow key={template.template_id}>
                  <TableCell className="font-medium text-foreground">{template.template_name}</TableCell>
                  <TableCell>
                    {template.active_version !== null ? (
                      <StatusBadge tone="success">v{template.active_version}</StatusBadge>
                    ) : (
                      <span className="text-caption text-muted-foreground">none</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {template.staged_version !== null ? (
                      <StatusBadge tone="warning">v{template.staged_version}</StatusBadge>
                    ) : (
                      <span className="text-caption text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {template.draft_version !== null ? (
                      <StatusBadge tone="neutral">v{template.draft_version}</StatusBadge>
                    ) : (
                      <span className="text-caption text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{template.versions_count}</TableCell>
                  <TableCell className="text-muted-foreground">{template.latest_version_created_at}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap justify-end gap-2">
                      <Link
                        to={`/sources/${sourceId}/templates/${template.template_id}`}
                        className={buttonVariants({ variant: 'outline', size: 'sm' })}
                      >
                        View
                      </Link>
                      {/* T5/T4-real: ingest a batch against this template's active mapping.
                          Gated on an active version existing (cannot ingest against a
                          never-activated template; the server also rejects with 409). */}
                      {template.active_version !== null ? (
                        <Link
                          to={`/sources/${sourceId}/templates/${template.template_id}/upload`}
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
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </section>
  )
}
