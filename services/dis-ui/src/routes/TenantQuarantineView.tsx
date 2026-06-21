import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CopyButton } from '../components/CopyButton'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import { useQuarantineDetail, useQuarantineList } from '../lib/dis-ui-server/quarantine-api'
import type {
  QuarantineFilters,
  StageWire,
  StatusWire,
  WindowWire,
} from '../lib/dis-ui-server/quarantine-api'

// Tenant Quarantine console (slice 15a, real endpoints). Scope is LOCKED to the caller's tenant
// (server-side, token only). The four filters drive server-side query params; the header badge
// shows the filter-INDEPENDENT open_count. Detail is addressed by the row's type-tagged id,
// round-tripped verbatim. Honest semantics: original_payload is always null (deferred); status
// "resolved" yields nothing (no resolve path, D82); there is NO resolve/dismiss/resubmit action.

type SourceFilter = string // 'all' or a source_id
type StageFilter = StageWire | 'all'
type StatusFilter = StatusWire | 'all'
type WindowFilter = WindowWire | 'all'

export function TenantQuarantineView({ snapshot }: { snapshot: AuthSnapshot | null }) {
  // The source filter is lazily pre-applied from the Dashboard ?source= deep link.
  const [searchParams] = useSearchParams()
  const [source, setSource] = useState<SourceFilter>(() => searchParams.get('source') ?? 'all')
  const [stage, setStage] = useState<StageFilter>('all')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [timeWindow, setTimeWindow] = useState<WindowFilter>('all')
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null)

  // 'all' -> undefined (no constraint); everything else is a real query param the server applies.
  const filters: QuarantineFilters = {
    source: source === 'all' ? undefined : source,
    errorType: stage === 'all' ? undefined : stage,
    status: status === 'all' ? undefined : status,
    window: timeWindow === 'all' ? undefined : timeWindow,
  }

  const list = useQuarantineList(snapshot, filters)
  const detail = useQuarantineDetail(snapshot, selectedItemId)

  const items = useMemo(() => list.data?.items ?? [], [list.data])
  // Source options come from the items, plus the currently-selected source so it stays visible
  // even when the server-filtered list no longer contains it.
  const sources = useMemo(() => {
    const byId = new Map<string, string>()
    for (const r of items) {
      byId.set(r.source_id, r.source)
    }
    if (source !== 'all' && !byId.has(source)) {
      byId.set(source, source)
    }
    return [...byId.entries()]
  }, [items, source])

  const anyFilterActive =
    source !== 'all' || stage !== 'all' || status !== 'all' || timeWindow !== 'all'

  if (list.isPending) {
    return <LoadingState label="Loading quarantine..." />
  }
  if (list.isError) {
    return (
      <ErrorState message="Could not load quarantined rows." onRetry={() => void list.refetch()} />
    )
  }

  const openCount = list.data?.open_count ?? 0

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-display">Quarantine</h1>
          <p className="text-caption text-muted-foreground">Failed rows and why they failed.</p>
        </div>
        <span className="text-caption text-muted-foreground">{openCount} open</span>
      </header>

      <div className="flex flex-wrap gap-3 text-sm">
        <label className="flex items-center gap-1">
          Source
          <Select
            aria-label="Source filter"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="h-7 w-auto"
          >
            <option value="all">All sources</option>
            {sources.map(([id, display]) => (
              <option key={id} value={id}>
                {display}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex items-center gap-1">
          Error type
          <Select
            aria-label="Error type filter"
            value={stage}
            onChange={(e) => setStage(e.target.value as StageFilter)}
            className="h-7 w-auto"
          >
            <option value="all">All errors</option>
            <option value="source-shape">source-shape</option>
            <option value="canonical-shape">canonical-shape</option>
            <option value="fk">fk</option>
            <option value="normalization">normalization</option>
            <option value="other">other</option>
          </Select>
        </label>
        <label className="flex items-center gap-1">
          Status
          <Select
            aria-label="Status filter"
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
            className="h-7 w-auto"
          >
            <option value="all">All</option>
            <option value="open">open</option>
            <option value="resolved">resolved</option>
          </Select>
        </label>
        <label className="flex items-center gap-1">
          Time
          <Select
            aria-label="Time range filter"
            value={timeWindow}
            onChange={(e) => setTimeWindow(e.target.value as WindowFilter)}
            className="h-7 w-auto"
          >
            <option value="all">All time</option>
            <option value="24h">Last 24 hours</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
          </Select>
        </label>
      </div>

      {items.length === 0 ? (
        anyFilterActive ? (
          <EmptyState
            title="No matching rows"
            message="No quarantined rows match the current filters."
          />
        ) : (
          <EmptyState
            title="No quarantined rows"
            message="Nothing has been quarantined for this tenant."
          />
        )
      ) : (
        <Card>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Error</TableHead>
                  <TableHead>Stage</TableHead>
                  <TableHead>Trace</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="text-muted-foreground">{row.failed_at}</TableCell>
                    <TableCell>{row.source}</TableCell>
                    <TableCell>{row.error_reason}</TableCell>
                    <TableCell>
                      <StatusBadge tone="warning">{row.failure_stage}</StatusBadge>
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => setSelectedItemId(row.id)}
                          className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                        >
                          {row.trace_id}
                        </button>
                        <CopyButton value={row.trace_id} label="Copy trace id" />
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {selectedItemId !== null ? (
        <Card>
          <CardHeader>
            <h2 className="text-subheading">Row detail</h2>
          </CardHeader>
          <CardContent>
            {detail.isPending ? (
              <LoadingState label="Loading detail..." />
            ) : detail.isError || detail.data === undefined ? (
              <ErrorState message="Could not load row detail." />
            ) : (
              <div className="text-sm">
                <p className="font-mono text-caption text-muted-foreground">
                  Trace: {detail.data.trace_id} · {detail.data.source} · {detail.data.failed_at}
                  {detail.data.mapping_version !== null ? ` · v${detail.data.mapping_version}` : ''}
                </p>
                <p className="mt-2">Error: {detail.data.error_reason}</p>
                <p>Stage: {detail.data.failure_stage}</p>
                <p>Context: {detail.data.error_context}</p>
                {/* original_payload is deferred this slice (always null): show an honest note
                    rather than a broken/empty section. */}
                <p className="mt-2 text-caption text-muted-foreground">
                  Original payload is not available yet.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      ) : null}
    </section>
  )
}
