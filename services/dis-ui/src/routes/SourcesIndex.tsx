import { Link } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import type { StatusTone } from '../components/StatusBadge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { sourceIdentity } from '../components/source-identity'
import { useSources } from '../lib/dis-ui-server/sources'
import type { SourceStatus } from '../lib/dis-ui-server/sources'
import { cn } from '@/lib/utils'

function statusTone(status: SourceStatus): StatusTone {
  if (status === 'active') {
    return 'success'
  }
  if (status === 'staged') {
    return 'warning'
  }
  if (status === 'failing') {
    return 'danger'
  }
  return 'neutral'
}

// Sources index (surface map screen 2), on the design-system craft bar: the tenant's
// sources as a carded table, status as a semantic badge, each row linking to its mapping
// versions. Create and per-row Edit live here; the Edit row links to SourceEdit, which is now
// the single per-source "Manage source" home (T6) where both edit and the soft Deprecate
// (active -> deprecated, no hard delete) live. This list keeps its read + Create + Edit role
// and stays routed for the notification deep-link and the post-create/edit landing.
export function SourcesIndex() {
  const { snapshot } = useAuth()
  const { data, isPending, isError, refetch } = useSources(snapshot)

  if (isPending) {
    return <LoadingState label="Loading sources..." />
  }
  if (isError || data === undefined) {
    return <ErrorState message="Could not load sources." onRetry={() => void refetch()} />
  }
  if (data.length === 0) {
    return (
      <section className="flex flex-col gap-6">
        <header className="flex items-baseline justify-between">
          <div>
            <h1 className="text-display">Manage sources</h1>
            <p className="text-caption text-muted-foreground">Your configured data sources.</p>
          </div>
          <Link to="/connectors/new" className={buttonVariants({ variant: 'default', size: 'sm' })}>
            New source
          </Link>
        </header>
        <EmptyState title="No sources" message="No configured sources for this tenant yet." />
      </section>
    )
  }

  return (
    <section className="flex flex-col gap-6">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-display">Manage sources</h1>
          <p className="text-caption text-muted-foreground">Your configured data sources.</p>
        </div>
        <Link to="/connectors/new" className={buttonVariants({ variant: 'default', size: 'sm' })}>
          New source
        </Link>
      </header>

      <Card>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Store</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Version</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((source) => {
                // Source-type identity (R1 helper, single mapping): the feed type keys the
                // identity (CSV -> the csv identity; others fall back to Other).
                const identity = sourceIdentity(source.type.toLowerCase())
                const Icon = identity.icon
                return (
                  <TableRow key={source.source_id}>
                    <TableCell className="font-medium text-foreground">
                      <span className="flex items-center gap-2">
                        <span
                          aria-hidden="true"
                          className={cn(
                            'flex size-6 items-center justify-center rounded-md',
                            identity.bgSoftClass,
                            identity.textClass,
                          )}
                        >
                          <Icon className="size-3.5" />
                        </span>
                        {source.name}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{source.type}</TableCell>
                    <TableCell className="text-muted-foreground">{source.store}</TableCell>
                    <TableCell>
                      <StatusBadge tone={statusTone(source.status)}>{source.status}</StatusBadge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      v{source.active_version}
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center justify-end gap-2">
                        <Link
                          to={`/sources/${source.source_id}/templates`}
                          className={buttonVariants({ variant: 'outline', size: 'sm' })}
                        >
                          Mappings
                        </Link>
                        <Link
                          to={`/sources/${source.source_id}/edit`}
                          className={buttonVariants({ variant: 'outline', size: 'sm' })}
                        >
                          Edit
                        </Link>
                      </span>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </section>
  )
}
