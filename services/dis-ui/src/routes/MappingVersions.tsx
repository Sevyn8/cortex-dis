import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import type { StatusTone } from '../components/StatusBadge'
import { useMappingVersion, useMappingVersions } from '../lib/dis-ui-server/mappings'
import type { MappingStatus } from '../lib/dis-ui-server/mappings'
import { cn } from '@/lib/utils'

function statusTone(status: MappingStatus): StatusTone {
  if (status === 'active') {
    return 'success'
  }
  if (status === 'staged') {
    return 'warning'
  }
  return 'neutral'
}

// Mapping Versions (surface map screen 6), TENANT slice, READ-ONLY listing, on the
// design-system craft bar. Version list (demand list 3.1) with status badges + a
// per-version full immutable definition (3.2). No edit / create / deprecate here (New
// version is a visibly-disabled affordance). A STAGED version links to Shadow Rollout
// Review (slice 22). No Audit-by-mapping_version_id link (needs 5.2 search).
export function MappingVersions() {
  const { sourceId } = useParams()
  const { snapshot } = useAuth()
  const list = useMappingVersions(snapshot, sourceId ?? null)
  const [selected, setSelected] = useState<number | null>(null)
  const detail = useMappingVersion(snapshot, sourceId ?? null, selected)

  if (sourceId === undefined) {
    return <EmptyState title="No source" message="No source id in the URL." />
  }
  if (list.isPending) {
    return <LoadingState label="Loading mappings..." />
  }
  if (list.isError) {
    return <ErrorState message="Could not load mapping versions." onRetry={() => void list.refetch()} />
  }
  if (list.data.length === 0) {
    return <EmptyState title="No mappings for this source" message={`No mapping versions for ${sourceId}.`} />
  }

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-display">Mappings: {sourceId}</h1>
          <p className="text-caption text-muted-foreground">Version history for this source.</p>
        </div>
        {/* FM2: read-only. Creating a new version is a Phase 2 affordance. */}
        <Button type="button" variant="outline" size="sm" disabled>
          New version (Phase 2)
        </Button>
      </header>

      <Card>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Version</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>By</TableHead>
                <TableHead>Fields</TableHead>
                <TableHead>Mapping rules</TableHead>
                <TableHead>Suite</TableHead>
                <TableHead>Active window</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.data.map((row) => (
                <TableRow key={row.version}>
                  <TableCell className="font-medium text-foreground">v{row.version}</TableCell>
                  <TableCell>
                    <StatusBadge tone={statusTone(row.status)}>{row.status.toUpperCase()}</StatusBadge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{row.created_at}</TableCell>
                  <TableCell className="text-muted-foreground">{row.created_by}</TableCell>
                  <TableCell>{row.field_count}</TableCell>
                  <TableCell>{row.transform_count}</TableCell>
                  <TableCell>v{row.suite_version}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {row.active_from === null ? '-' : `${row.active_from} to ${row.active_to ?? 'current'}`}
                  </TableCell>
                  <TableCell>
                    <span className="flex items-center gap-3">
                      <button
                        type="button"
                        onClick={() => setSelected(row.version)}
                        className="text-primary underline-offset-4 hover:underline"
                      >
                        View
                      </button>
                      {/* A staged version is reviewable in shadow rollout (slice 22). */}
                      {row.status === 'staged' ? (
                        <Link
                          to={`/sources/${sourceId}/shadow`}
                          className={cn(buttonVariants({ variant: 'outline', size: 'xs' }))}
                        >
                          Review shadow rollout
                        </Link>
                      ) : null}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {selected !== null ? (
        <Card>
          <CardHeader>
            <h2 className="text-subheading">Definition (v{selected}, immutable)</h2>
          </CardHeader>
          <CardContent>
            {detail.isPending ? (
              <LoadingState label="Loading definition..." />
            ) : detail.isError || detail.data === null || detail.data === undefined ? (
              <ErrorState message="Could not load the mapping definition." />
            ) : (
              <pre className="overflow-x-auto rounded-md border border-border bg-muted/50 p-2 text-xs">
                {JSON.stringify(detail.data.mapping_rules, null, 2)}
              </pre>
            )}
          </CardContent>
        </Card>
      ) : null}
    </section>
  )
}
