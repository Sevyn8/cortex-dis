import { useState } from 'react'
import type { FormEvent } from 'react'

import { isOps } from '../auth/AuthSnapshot'
import { useAuth } from '../auth/useAuth'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CopyButton } from '../components/CopyButton'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import { useAuditTrace } from '../lib/dis-ui-server/audit'
import type { AuditTrace } from '../lib/dis-ui-server/audit'
import { useCrossTenantAuditTrace } from '../lib/dis-ui-server/ops-cross-tenant'

// Audit and Trace Lookup (surface map screen 8), on the design-system craft bar.
// trace_id direct lookup (demand list 5.1): enter a trace_id, render its ordered
// per-stage lifecycle. ONE scope-aware screen (slice 25 / T9): in TENANT mode the scope is
// LOCKED to the caller's tenant (own-tenant lookup; a foreign trace returns not-found). In
// OPS mode (isOps) it does a cross-tenant lookup (5.2) and adds a tenant field to the result.
// The richer 5.2 search (by source/stage/time) is a flagged seam, not built.
//
// AUTHORIZATION (T9): the cross-tenant lookup is requested ONLY when isOps (useCrossTenantAuditTrace
// is gated on `ops`); the tenant getter returns null for any trace whose tenant_id is not the
// caller's. This UI gating is necessary BUT NOT SUFFICIENT - the real boundary is server-enforced:
// the backend MUST refuse cross-tenant lookups for a non-ops token and RLS-scope tenant queries.
export function AuditLookup() {
  const { snapshot } = useAuth()
  const ops = snapshot !== null && isOps(snapshot)
  const [input, setInput] = useState('')
  const [queried, setQueried] = useState<string | null>(null)

  // Dual-hook gate (see QuarantineConsole): both run; only the active mode's is read, so
  // the tenant path stays byte-for-byte. Fields are read off the active query directly
  // (not a unioned result type) to keep types clean; the tenant name comes from the
  // cross-tenant query (ops only).
  const tenantTrace = useAuditTrace(snapshot, ops ? null : queried)
  const crossTrace = useCrossTenantAuditTrace(queried, ops)
  const pending = ops ? crossTrace.isPending : tenantTrace.isPending
  const errored = ops ? crossTrace.isError : tenantTrace.isError
  const data: AuditTrace | null | undefined = ops ? crossTrace.data : tenantTrace.data
  const tenant = ops && crossTrace.data ? crossTrace.data : null

  function submit(event: FormEvent): void {
    event.preventDefault()
    const trimmed = input.trim()
    setQueried(trimmed.length === 0 ? null : trimmed)
  }

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h1 className="text-display">Audit and Trace Lookup</h1>
        <p className="text-caption text-muted-foreground">
          {ops ? 'Trace a row across tenants through its lifecycle.' : 'Trace a row through its lifecycle.'}
        </p>
      </header>

      <form onSubmit={submit} className="flex items-end gap-2">
        <div>
          <Label htmlFor="trace-id">Trace ID</Label>
          <Input
            id="trace-id"
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="mt-1 w-80 font-mono"
          />
        </div>
        <Button type="submit">Look up</Button>
      </form>

      <div>{renderResult()}</div>
    </section>
  )

  function renderResult() {
    if (queried === null) {
      return <p className="text-caption text-muted-foreground">Enter a trace id to look up its lifecycle.</p>
    }
    if (pending) {
      return <LoadingState label="Looking up trace..." />
    }
    if (errored) {
      return <ErrorState message="Could not look up the trace." />
    }
    if (data === null || data === undefined) {
      return <EmptyState title="Trace not found" message={`No trace ${queried} for this tenant.`} />
    }
    const result = data
    return (
      <Card>
        <CardContent>
          {tenant !== null ? (
            <p className="text-caption">
              <span className="text-label text-muted-foreground">Tenant</span> {tenant.tenant_name} (
              {tenant.tenant_id})
            </p>
          ) : null}
          <p className="flex items-center gap-1 font-mono text-caption text-muted-foreground">
            {result.trace_id} · {result.source_id}
            <CopyButton value={result.trace_id} label="Copy trace id" />
          </p>
          <Table className="mt-2">
            <TableHeader>
              <TableRow>
                <TableHead>Outcome</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead>At</TableHead>
                <TableHead>Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.stages.map((stage) => (
                <TableRow key={stage.stage}>
                  <TableCell>
                    <StatusBadge tone={stage.status === 'ok' ? 'success' : 'danger'}>
                      {stage.status === 'ok' ? 'OK' : 'FAIL'}
                    </StatusBadge>
                  </TableCell>
                  <TableCell className="font-medium text-foreground">{stage.stage}</TableCell>
                  <TableCell className="text-muted-foreground">{stage.at}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {stage.mapping_version_id !== undefined ? `v${stage.mapping_version_id}` : ''}
                    {stage.mapping_version_id !== undefined && stage.error_code !== undefined ? ' · ' : ''}
                    {stage.error_code !== undefined ? stage.error_code : ''}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    )
  }
}
