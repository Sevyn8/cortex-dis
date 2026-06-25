import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { Button, buttonVariants } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { EmptyState } from '../../components/states/EmptyState'
import { ErrorState } from '../../components/states/ErrorState'
import { LoadingState } from '../../components/states/LoadingState'
import { StatusBadge } from '../../components/StatusBadge'
import type { StatusTone } from '../../components/StatusBadge'
import { useDrafts } from '../../lib/dis-ui-server/atlas'
import type { DraftStatusFilter, DraftSummary } from '../../lib/dis-ui-server/atlas'

// The verticals/drafts registry (A4 PR3b): the lean DraftSummary list from GET /atlas/drafts,
// with a server-side status filter (?status=). A draft row links to the ratify console; a
// published row links to the read-only published detail.

const STATUS_OPTIONS: Array<{ value: '' | DraftStatusFilter; label: string }> = [
  { value: '', label: 'All statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'published', label: 'Published' },
  { value: 'superseded', label: 'Superseded' },
]

function statusTone(status: string): StatusTone {
  if (status === 'published') {
    return 'success'
  }
  if (status === 'superseded') {
    return 'neutral'
  }
  return 'warning'
}

function rowTarget(row: DraftSummary): string {
  return row.status === 'published'
    ? `/atlas/published/${row.draft_id}`
    : `/atlas/drafts/${row.draft_id}`
}

export function AtlasRegistry() {
  const navigate = useNavigate()
  const [filter, setFilter] = useState<'' | DraftStatusFilter>('')
  // Server-side filter: undefined -> all, else ?status=. The hook re-queries on the key change.
  const query = useDrafts(filter === '' ? undefined : filter)

  const header = (
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-col gap-1">
        <h1 className="text-display">Atlas schemas</h1>
        <p className="text-caption text-muted-foreground">
          Canonical-schema drafts and published versions across verticals.
        </p>
      </div>
      <div className="flex items-center gap-3">
        <Select
          aria-label="Filter by status"
          value={filter}
          onChange={(e) => setFilter(e.target.value as '' | DraftStatusFilter)}
          className="h-8 w-auto"
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
        <Link to="/atlas/upload" className={buttonVariants({ variant: 'default', size: 'sm' })}>
          New draft
        </Link>
      </div>
    </header>
  )

  if (query.isPending) {
    return <LoadingState label="Loading schemas..." />
  }
  if (query.isError) {
    return (
      <ErrorState
        message="Could not load the schema registry."
        onRetry={() => void query.refetch()}
      />
    )
  }

  return (
    <section className="flex flex-col gap-6">
      {header}

      {query.data.length === 0 ? (
        <EmptyState
          title="No schemas yet"
          message="Upload example exports to infer a draft schema."
        >
          <Link to="/atlas/upload" className={buttonVariants({ variant: 'default', size: 'sm' })}>
            New draft
          </Link>
        </EmptyState>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Vertical</TableHead>
              <TableHead>Table</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Version</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {query.data.map((row) => (
              <TableRow key={row.draft_id}>
                <TableCell className="font-mono">{row.vertical}</TableCell>
                <TableCell className="font-mono">{row.table_key}</TableCell>
                <TableCell>
                  <StatusBadge tone={statusTone(row.status)}>{row.status}</StatusBadge>
                </TableCell>
                <TableCell>v{row.schema_version}</TableCell>
                <TableCell className="text-right">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => navigate(rowTarget(row))}
                  >
                    {row.status === 'published' ? 'View' : 'Ratify'}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  )
}
