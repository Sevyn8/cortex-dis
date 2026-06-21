import { useQuery } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { SERVER_MODE } from './mode'
import { QUARANTINE_TRACE_IDS } from './quarantine'

// trace_ids are UUIDv7 (dis-core trace_id.py). Synthetic UUIDv7-shaped fixture for
// the healthy trace; the quarantined trace reuses QUARANTINE_TRACE_IDS.acmeCanonical
// (imported) so the same id appears in both the Quarantine and Audit screens.
export const AUDIT_HEALTHY_TRACE_ID = '0190ac0e-1a01-7001-8a01-000000000010'

// Audit trace lookup (demand list 5.1), tenant slice. Fixture mode (default)
// returns the inlined fixtures; real mode is OPEN (slice 13) and throws. Shapes
// are PROVISIONAL pending Sanjeev's slices 15-17.

// PROVISIONAL: the demand list gives the healthy stage object shape and an example
// sequence, but does not pin the full stage vocabulary/order or the error_code
// values. `quarantined` is modeled as a terminal stage with status 'error' and an
// error_code (5.1: "Quarantined traces end at a `quarantined` stage with error_code").
export type AuditStage = {
  stage: string
  at: string
  status: string
  mapping_version_id?: number
  error_code?: string
}

export type AuditTrace = {
  trace_id: string
  tenant_id: string | null
  source_id: string
  stages: AuditStage[]
  prior_trace_id: string | null
}

// Both traces belong to the primary tenant and the real seeded source
// `manual_csv_upload` (kind-style composite key; no invented src_* id). The
// quarantined trace reuses QUARANTINE_TRACE_IDS.acmeCanonical (the Quarantine row)
// so the two screens cross-reference on the same UUIDv7.
const AUDIT_FIXTURES: Record<string, AuditTrace> = {
  [AUDIT_HEALTHY_TRACE_ID]: {
    trace_id: AUDIT_HEALTHY_TRACE_ID,
    tenant_id: 't_acme9k2l1mn4',
    source_id: 'manual_csv_upload',
    stages: [
      { stage: 'received', at: '2026-06-03T09:00:01Z', status: 'ok' },
      { stage: 'validated', at: '2026-06-03T09:00:02Z', status: 'ok' },
      { stage: 'mapped', at: '2026-06-03T09:00:03Z', status: 'ok', mapping_version_id: 1 },
      { stage: 'committed', at: '2026-06-03T09:00:04Z', status: 'ok' },
    ],
    prior_trace_id: null,
  },
  [QUARANTINE_TRACE_IDS.acmeCanonical]: {
    trace_id: QUARANTINE_TRACE_IDS.acmeCanonical,
    tenant_id: 't_acme9k2l1mn4',
    source_id: 'manual_csv_upload',
    stages: [
      { stage: 'received', at: '2026-06-03T09:08:00Z', status: 'ok' },
      { stage: 'validated', at: '2026-06-03T09:08:01Z', status: 'ok' },
      { stage: 'mapped', at: '2026-06-03T09:08:02Z', status: 'ok', mapping_version_id: 1 },
      { stage: 'quarantined', at: '2026-06-03T09:08:03Z', status: 'error', error_code: 'CANONICAL_SHAPE_INVALID' },
    ],
    prior_trace_id: null,
  },
}

// Direct trace lookup, tenant-scoped (own-tenant only, per the surface map's
// RLS-scoped tenant audit). Returns null for an unknown trace or a trace owned by
// another tenant - a not-found result the UI renders as the empty state, distinct
// from a thrown query error.
export async function getAuditTrace(
  snapshot: AuthSnapshot,
  traceId: string,
): Promise<AuditTrace | null> {
  if (SERVER_MODE === 'real') {
    throw new Error('real-mode getAuditTrace() is not implemented (slice 13)')
  }
  const trace = AUDIT_FIXTURES[traceId]
  if (trace === undefined || trace.tenant_id !== snapshot.tenantId) {
    return null
  }
  return trace
}

export function useAuditTrace(snapshot: AuthSnapshot | null, traceId: string | null) {
  return useQuery({
    queryKey: ['dis-ui-server', 'audit', snapshot?.tenantId ?? 'none', traceId ?? 'none'],
    queryFn: () => getAuditTrace(snapshot as AuthSnapshot, traceId as string),
    enabled: snapshot !== null && traceId !== null,
    staleTime: Infinity,
    retry: false,
  })
}
