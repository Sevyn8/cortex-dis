import { useParams } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import {
  usePromoteShadow,
  useRejectShadow,
  useShadowDiff,
  useShadowStats,
} from '../lib/dis-ui-server/shadow'

// Shadow Rollout Review (surface map screen 5), TENANT slice, on the design-system
// craft bar. Onboarding step 3: review a STAGED mapping version's shadow output
// (demand list 2.6 stats as metric cards, 2.7 diff as a carded table) and Promote
// (2.8) or Reject (2.9). Reached only from a source that has a staged version (FM4); a
// source with no staged version renders the empty state. Tenant scoped, no ops surface
// (FM3). "Extend window" is omitted (no endpoint in 2.6-2.9). Behavior is unchanged.
export function Shadow() {
  const { sourceId } = useParams()
  const { snapshot } = useAuth()
  const stats = useShadowStats(snapshot, sourceId ?? null)
  const diff = useShadowDiff(snapshot, sourceId ?? null)
  const promote = usePromoteShadow(snapshot, sourceId ?? null)
  const reject = useRejectShadow(snapshot, sourceId ?? null)

  if (sourceId === undefined) {
    return <EmptyState title="No source" message="No source id in the URL." />
  }
  if (stats.isPending) {
    return <LoadingState label="Loading shadow rollout..." />
  }
  if (stats.isError) {
    return <ErrorState message="Could not load the shadow rollout." onRetry={() => void stats.refetch()} />
  }
  if (stats.data === null) {
    return (
      <EmptyState
        title="No staged version"
        message={`${sourceId} has no mapping version under shadow review.`}
      />
    )
  }

  const s = stats.data
  const busy = promote.isPending || reject.isPending
  const metrics: { label: string; value: string; sub?: string }[] = [
    { label: 'Window', value: s.window, sub: `${s.input_chunks} input chunks` },
    { label: 'Staged output', value: `${s.staged_rows} rows` },
    {
      label: 'Validation pass rate',
      value: `${(s.validation_pass_rate * 100).toFixed(1)}%`,
      sub: `${s.validation_fail_count} fails`,
    },
    s.active_version !== null
      ? { label: `Diff vs active v${s.active_version}`, value: `${s.diff_differing} differ`, sub: `${s.diff_identical} identical · ${s.diff_column}` }
      : { label: 'Diff', value: 'First onboarding', sub: 'no prior active version' },
  ]

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h1 className="text-display">
          Shadow review: {sourceId} (v{s.staged_version} staged
          {s.active_version !== null ? ` vs v${s.active_version} active` : ''})
        </h1>
        <p className="text-caption text-muted-foreground">Step 3 of 3: review shadow output, then promote or iterate.</p>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {metrics.map((m) => (
          <Card key={m.label}>
            <CardContent>
              <div className="text-label text-muted-foreground">{m.label}</div>
              <div className="text-heading">{m.value}</div>
              {m.sub !== undefined ? <div className="text-caption text-muted-foreground">{m.sub}</div> : null}
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardContent>
          <h2 className="text-subheading mb-2">Diff samples (staged vs active)</h2>
          {diff.isPending ? (
            <LoadingState label="Loading diff..." />
          ) : diff.isError ? (
            <ErrorState message="Could not load the diff sample." onRetry={() => void diff.refetch()} />
          ) : diff.data.length === 0 ? (
            <p className="text-caption text-muted-foreground">No diff sample (no prior active version).</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>SKU</TableHead>
                  <TableHead>Column</TableHead>
                  <TableHead>Active</TableHead>
                  <TableHead>Staged</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {diff.data.map((row) => (
                  <TableRow key={row.sku_id}>
                    <TableCell className="font-medium text-foreground">{row.sku_id}</TableCell>
                    <TableCell className="font-mono text-xs">{row.column}</TableCell>
                    <TableCell className="text-muted-foreground">{row.active_value}</TableCell>
                    <TableCell>{row.staged_value}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="flex gap-3">
        <Button type="button" onClick={() => promote.mutate()} disabled={busy}>
          Promote to active
        </Button>
        <Button type="button" variant="outline" onClick={() => reject.mutate()} disabled={busy}>
          Reject, iterate
        </Button>
      </div>
    </section>
  )
}
