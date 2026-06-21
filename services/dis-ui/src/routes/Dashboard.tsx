import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useMappingTemplates } from '../lib/dis-ui-server/mapping-templates'
import type { MappingTemplate } from '../lib/dis-ui-server/mapping-templates'
import { useTemplateTypes } from '../lib/dis-ui-server/template-types'
import { useDashboardMetrics } from '../lib/dis-ui-server/dashboard'
import type { FlowRow, QuarantineMetrics } from '../lib/dis-ui-server/dashboard'

// Tenant-admin Dashboard (/), an HONEST skeleton: real data where an endpoint exists, muted
// placeholders everywhere else. NO fabricated numbers. Real today: the pipelines list + count
// and template-type labels (GET /mapping-templates, /template-types), and the KPI + Flow metrics
// (GET /dashboard/metrics: 24h ingest, quarantine counts, canonical record count, per-template
// flow). Only the Quality panel stays pending (no data-quality metrics endpoint yet).

// A REAL KPI tile (a value backed by an endpoint), with an optional small sub-line for context
// (e.g. the quarantine raw denominator). `value` is rendered prominently; `sub` muted beneath.
function MetricTile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <Card>
      <CardContent>
        <div className="text-label text-muted-foreground">{label}</div>
        <div className="text-display-lg tabular-nums">{value}</div>
        {sub !== undefined ? <div className="text-caption text-muted-foreground">{sub}</div> : null}
      </CardContent>
    </Card>
  )
}

// A panel for a section that has no data source yet: a muted, visually-distinct "coming soon".
function PendingPanel({ title, message }: { title: string; message: string }) {
  return (
    <Card className="border-dashed bg-muted/30">
      <CardHeader>
        <CardTitle className="text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-caption text-muted-foreground">{message}</p>
      </CardContent>
    </Card>
  )
}

// The quarantine tile's sub-line: lead with the raw count (the big value), explain the
// denominator + approximate rate here. "no ingest" when nothing was received (rate is null).
function quarantineSub(q: QuarantineMetrics): string {
  if (q.received_rows === 0) {
    return 'no ingest in the last 24h'
  }
  const pct = (q.rate ?? 0) * 100
  return `of ${q.received_rows.toLocaleString()} rows received (${pct.toFixed(2)}%)`
}

// Flow: per-template recent ingest volume + last-received (real, from /dashboard/metrics). The
// template name is resolved from the mapping-templates the dashboard already lists; an unmatched
// id falls back to the raw id. Honest empty / loading / error states, never fabricated rows.
function FlowPanel({
  flow,
  isError,
  templateName,
}: {
  flow: FlowRow[] | undefined
  isError: boolean
  templateName: Map<string, string>
}) {
  let body: ReactNode
  if (isError) {
    body = <p className="text-caption text-muted-foreground">Flow metrics are unavailable.</p>
  } else if (flow === undefined) {
    body = <p className="text-caption text-muted-foreground">Loading flow...</p>
  } else if (flow.length === 0) {
    body = <p className="text-caption text-muted-foreground">No ingest in the last 24h.</p>
  } else {
    body = (
      <ul className="flex flex-col gap-3">
        {flow.map((row, index) => {
          const name =
            (row.template_id !== null ? templateName.get(row.template_id) : null) ??
            row.template_id ??
            'Unknown template'
          return (
            <li key={row.template_id ?? `flow-${index}`} className="flex flex-col gap-0.5">
              <span className="text-body-strong break-all text-foreground">{name}</span>
              <span className="text-caption text-muted-foreground">
                {row.rows_24h.toLocaleString()} rows in 24h
                {row.last_received_at !== null ? ` · last received ${row.last_received_at}` : ''}
              </span>
            </li>
          )
        })}
      </ul>
    )
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Flow</CardTitle>
        <p className="text-caption text-muted-foreground">
          Recent ingest volume per template (last 24h).
        </p>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  )
}

export function Dashboard() {
  const { snapshot } = useAuth()
  // The only real tenant-scoped read the dashboard has today: the mapping-template lineages.
  const templates = useMappingTemplates(snapshot, null)
  // Friendly template-type labels (key -> display_name), verbatim from GET /template-types.
  const types = useTemplateTypes()
  // The KPI + Flow metrics (GET /dashboard/metrics). Independent of the templates read, so a
  // metrics failure degrades only the tiles, never the pipelines table.
  const metricsQuery = useDashboardMetrics(snapshot)
  const typeLabels = new Map<string, string>()
  for (const t of types.data ?? []) {
    typeLabels.set(t.key, t.display_name)
  }
  function typeLabel(template: MappingTemplate): string | null {
    if (template.template_type === undefined) {
      return null
    }
    return typeLabels.get(template.template_type) ?? null
  }

  const header = (
    <header className="flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h1 className="text-display">Dashboard</h1>
        <p className="text-caption text-muted-foreground">
          Is your data flowing, and landing clean?
        </p>
      </div>
      <Link to="/connectors/new" className={buttonVariants({ variant: 'default', size: 'sm' })}>
        Add source
      </Link>
    </header>
  )

  if (templates.isPending) {
    return <LoadingState label="Loading dashboard..." />
  }
  if (templates.isError) {
    return (
      <ErrorState
        message="Could not load the dashboard."
        onRetry={() => void templates.refetch()}
      />
    )
  }

  const list = templates.data
  const activePipelines = list.filter((t) => t.active_version !== null).length

  // Metric tiles degrade independently: the value when loaded, a muted fallback otherwise.
  const metrics = metricsQuery.data
  const metricsError = metricsQuery.isError
  const metricsReady = metrics !== undefined && !metricsError
  function kpi(node: ReactNode): ReactNode {
    if (metricsError) {
      return <span className="text-body text-muted-foreground">Unavailable</span>
    }
    if (metrics === undefined) {
      return <span className="text-body text-muted-foreground">Loading</span>
    }
    return node
  }
  const templateName = new Map(list.map((t) => [t.template_id, t.template_name] as const))

  return (
    <section className="flex flex-col gap-6">
      {header}

      {/* KPI strip: all four real (GET /mapping-templates + /dashboard/metrics). Records in
          canonical replaced the freshness tile (freshness needs an expected-cadence model). */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricTile label="Active pipelines" value={activePipelines} />
        <MetricTile
          label="Rows ingested (24h)"
          value={kpi(metrics ? metrics.rows_ingested_24h.toLocaleString() : null)}
        />
        <MetricTile
          label="Quarantined (24h)"
          value={kpi(metrics ? metrics.quarantine_24h.quarantined_rows.toLocaleString() : null)}
          sub={
            metrics !== undefined && !metricsError
              ? quarantineSub(metrics.quarantine_24h)
              : undefined
          }
        />
        <MetricTile
          label="Records in canonical"
          value={kpi(metrics ? metrics.records_in_canonical.total.toLocaleString() : null)}
          sub={metricsReady ? 'rows live in the platform' : undefined}
        />
      </div>

      {/* Needs attention: no data-quality metrics yet, so no fabricated alerts. */}
      <Card className="border-dashed bg-muted/30">
        <CardHeader>
          <CardTitle className="text-muted-foreground">Needs attention</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-caption text-muted-foreground">
            Alerts appear here once data-quality metrics are available.
          </p>
        </CardContent>
      </Card>

      {/* Flow is REAL (per-template 24h volume from /dashboard/metrics); Quality stays pending
          (no data-quality metrics endpoint yet). */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <FlowPanel flow={metrics?.flow} isError={metricsError} templateName={templateName} />
        <PendingPanel
          title="Quality"
          message="Pass rate and rejection reasons will appear here once data-quality metrics are available."
        />
      </div>

      {/* Pipelines: REAL from GET /mapping-templates. Health is neutral (no quality data to color
          it). No "Last received" column - that needs an upload-history endpoint (see Flow). */}
      <Card>
        <CardHeader>
          <CardTitle>Pipelines</CardTitle>
        </CardHeader>
        <CardContent>
          {list.length === 0 ? (
            <p className="text-caption text-muted-foreground">
              No pipelines yet. Add a source to create one.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Health</TableHead>
                    <TableHead>Pipeline</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.map((template) => (
                    <PipelineRow
                      key={template.template_id}
                      template={template}
                      typeLabel={typeLabel(template)}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  )
}

function PipelineRow({
  template,
  typeLabel,
}: {
  template: MappingTemplate
  typeLabel: string | null
}) {
  const noVersions =
    template.active_version === null &&
    template.staged_version === null &&
    template.draft_version === null
  return (
    <TableRow>
      <TableCell>
        {/* Neutral health: no quality/flow data exists yet to make this meaningful. */}
        <span
          aria-hidden="true"
          className="inline-block size-2 rounded-full bg-muted-foreground/40"
        />
        <span className="sr-only">Health data pending</span>
      </TableCell>
      <TableCell className="font-medium text-foreground">
        <Link
          to={`/sources/${template.source_id}/templates/${template.template_id}`}
          className="text-primary underline-offset-4 hover:underline"
        >
          {template.template_name}
        </Link>
      </TableCell>
      <TableCell>
        {typeLabel !== null ? (
          <StatusBadge tone="neutral">{typeLabel}</StatusBadge>
        ) : (
          <span className="text-caption text-muted-foreground">-</span>
        )}
      </TableCell>
      <TableCell>
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
          {noVersions ? (
            <span className="text-caption text-muted-foreground">No versions</span>
          ) : null}
        </div>
      </TableCell>
    </TableRow>
  )
}
