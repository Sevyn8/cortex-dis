import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { CHAIN_DEPTH_CAP, QUARANTINE_TRACE_IDS } from './quarantine'
import type {
  QuarantineDetail,
  QuarantineRow,
  ResubmitRecord,
  ResubmitRequest,
  ResubmitResponse,
} from './quarantine'
import type { AuditTrace } from './audit'
import { SERVER_MODE } from './mode'

// Cross-tenant ops shapes (slice 25): the ops MODES of Quarantine and Audit read across
// tenants. Every shape here is PROVISIONAL and lives only in this single containment file
// (FM2), naming demand list 4.1 (cross-tenant quarantine list), 4.3 (tenant-context
// resubmit) and 5.2 (cross-tenant trace lookup). This file imports only TYPES and the
// CHAIN_DEPTH_CAP constant from the tenant fixtures - it never edits or mutates them, so
// tenant mode stays byte-for-byte (FM1).
//
// CROSS-TENANT AUTHORIZATION IS SERVER-ENFORCED, NOT MODELED HERE. The UI requests fleet
// scope ONLY when isOps (the fleet hooks below are gated by an `enabled` flag the screens set
// to the isOps result), and the tenant-scoped getters in quarantine.ts / audit.ts key strictly
// on snapshot.tenantId. That UI gating is NECESSARY BUT NOT SUFFICIENT: the real boundary is
// the backend, which MUST refuse fleet scope for a non-ops (non-PLATFORM) token and RLS-scope
// every tenant query so a tenant token can never read another tenant's rows. Whether an ops
// token may also ACT (resubmit) in another tenant's context is Sanjeev's RLS/policy call (open
// item) - not invented. These fixtures return multi-tenant data only because the caller is ops.

// 4.1 cross-tenant variant: a fleet-wide quarantine row carries its tenant.
export type FleetQuarantineRow = QuarantineRow & { tenant_id: string; tenant_name: string }

// 4.3 cross-tenant variant: demand list 4.3 PINS the body to { resubmit_type,
// parent_trace_id } with NO tenant_id. Acting in a tenant's context is not covered by
// 4.3, so the cross-tenant resubmit the UI defines ADDS tenant_id (provisional, flagged).
// The tenant-mode ResubmitRequest is untouched.
export type OpsResubmitRequest = ResubmitRequest & { tenant_id: string }

// 5.2 cross-tenant lookup result: the tenant-scoped AuditTrace plus a display tenant_name.
export type CrossTenantAuditTrace = AuditTrace & { tenant_name: string }

// Grounded external-string tenant ids, consistent with the slice-24 Fleet fixture
// (ops-fleet.ts). Only t_acme9k2l1mn4 is the real persona tenant; the others are
// provisional display fixtures (external t_* form; external<->UUID is OPEN, D37).
const TENANTS = {
  acme: { id: 't_acme9k2l1mn4', name: 'Acme Retail' },
  beta: { id: 't_beta7h2k9m3n', name: 'Beta Stores' },
  delta: { id: 't_delta2s9t5v3', name: 'Delta Foods' },
} as const

// Synthetic UUIDv7-shaped trace ids for the non-Acme fleet rows (continue the
// QUARANTINE_TRACE_IDS numbering). Acme rows reuse QUARANTINE_TRACE_IDS. Exported so the
// ops-mode tests can address specific cross-tenant rows.
export const OPS_QUARANTINE_TRACE_IDS = {
  betaCanonical: '0190ac0e-1a01-7001-8a01-000000000201',
  deltaFk: '0190ac0e-1a01-7001-8a01-000000000202',
} as const
export const OPS_AUDIT_TRACE_IDS = {
  betaHealthy: '0190ac0e-1a01-7001-8a01-000000000210',
} as const
const BETA_TRACE = OPS_QUARANTINE_TRACE_IDS.betaCanonical
const DELTA_TRACE = OPS_QUARANTINE_TRACE_IDS.deltaFk
const BETA_AUDIT_TRACE = OPS_AUDIT_TRACE_IDS.betaHealthy

// Bounded fleet-wide quarantine list (4.1, cross-tenant). A real fleet list could be
// large; pagination is a seam, not built (FM5).
const FLEET_QUARANTINE: FleetQuarantineRow[] = [
  {
    tenant_id: TENANTS.acme.id,
    tenant_name: TENANTS.acme.name,
    trace_id: QUARANTINE_TRACE_IDS.acmeCanonical,
    source_id: 'manual_csv_upload',
    source: 'Manual CSV Upload',
    store: 'Acme Downtown #1',
    error_reason: "price '12.5o' not a valid number",
    failure_stage: 'canonical-shape',
    mapping_version: 1,
    failed_at: '2026-06-03T09:08:00Z',
    status: 'open',
  },
  {
    tenant_id: TENANTS.acme.id,
    tenant_name: TENANTS.acme.name,
    trace_id: QUARANTINE_TRACE_IDS.acmeNormalization,
    source_id: 'manual_csv_upload',
    source: 'Manual CSV Upload',
    store: 'Acme Downtown #1',
    error_reason: 'bad date format in source_sale_timestamp',
    failure_stage: 'normalization',
    mapping_version: 1,
    failed_at: '2026-06-03T09:05:00Z',
    status: 'open',
  },
  {
    tenant_id: TENANTS.beta.id,
    tenant_name: TENANTS.beta.name,
    trace_id: BETA_TRACE,
    source_id: 'square_pos_v1',
    source: 'Square POS',
    store: 'Beta Midtown',
    error_reason: "qty 'NaN' not an integer",
    failure_stage: 'canonical-shape',
    mapping_version: 2,
    failed_at: '2026-06-04T07:40:00Z',
    status: 'open',
  },
  {
    tenant_id: TENANTS.delta.id,
    tenant_name: TENANTS.delta.name,
    trace_id: DELTA_TRACE,
    source_id: 'erp_daily',
    source: 'ERP Daily',
    store: 'Delta Warehouse',
    error_reason: 'store_id not found in identity_mirror',
    failure_stage: 'fk',
    mapping_version: 1,
    failed_at: '2026-06-04T06:02:00Z',
    status: 'open',
  },
]

// Base detail per fleet trace (chain_depth is the BASE; the ops resubmit store overlays
// the effective depth). acmeNormalization is seeded at the cap to exercise the disabled
// state. Reuses the QuarantineDetail shape.
type FleetDetailBase = Omit<QuarantineDetail, 'resubmits'>

const FLEET_DETAIL: Record<string, FleetDetailBase> = {
  [QUARANTINE_TRACE_IDS.acmeCanonical]: {
    trace_id: QUARANTINE_TRACE_IDS.acmeCanonical,
    source: 'Manual CSV Upload',
    store: 'Acme Downtown #1',
    failed_at: '2026-06-03T09:08:00Z',
    failure_stage: 'canonical-shape',
    mapping_version: 1,
    error_reason: "price '12.5o' not a valid number",
    error_context: 'canonical-shape validation: column price failed numeric cast',
    original_payload: { sku: 'A123', price: '12.5o', qty: '3', txn_date: '03-12-25' },
    chain_depth: 0,
  },
  [QUARANTINE_TRACE_IDS.acmeNormalization]: {
    trace_id: QUARANTINE_TRACE_IDS.acmeNormalization,
    source: 'Manual CSV Upload',
    store: 'Acme Downtown #1',
    failed_at: '2026-06-03T09:05:00Z',
    failure_stage: 'normalization',
    mapping_version: 1,
    error_reason: 'bad date format in source_sale_timestamp',
    error_context: 'normalization: column source_sale_timestamp unparseable (expected ISO-8601)',
    original_payload: { sku: 'A777', price: '5.00', qty: '2', txn_date: '2026/06/03 garbage' },
    chain_depth: CHAIN_DEPTH_CAP,
  },
  [BETA_TRACE]: {
    trace_id: BETA_TRACE,
    source: 'Square POS',
    store: 'Beta Midtown',
    failed_at: '2026-06-04T07:40:00Z',
    failure_stage: 'canonical-shape',
    mapping_version: 2,
    error_reason: "qty 'NaN' not an integer",
    error_context: 'canonical-shape validation: column qty failed integer cast',
    original_payload: { sku: 'B900', price: '4.50', qty: 'NaN' },
    chain_depth: 0,
  },
  [DELTA_TRACE]: {
    trace_id: DELTA_TRACE,
    source: 'ERP Daily',
    store: 'Delta Warehouse',
    failed_at: '2026-06-04T06:02:00Z',
    failure_stage: 'fk',
    mapping_version: 1,
    error_reason: 'store_id not found in identity_mirror',
    error_context: 'fk validation: store_id has no identity_mirror.stores row',
    original_payload: { sku: 'D100', price: '12.00', qty: '1', store_id: 'unknown-99' },
    chain_depth: 0,
  },
}

// Mutable ops resubmit store (notifications/quarantine pattern), keyed by trace_id.
let opsResubmitStore: Record<string, ResubmitRecord[]> = {}

// Test-only: clear recorded ops resubmits so mutations do not bleed between tests.
export function __resetOpsCrossTenantFixture(): void {
  opsResubmitStore = {}
}

const OPS_RESUBMIT_CHILD_TRACE_IDS = [
  '0190ac0e-1a01-7001-8a01-000000000301',
  '0190ac0e-1a01-7001-8a01-000000000302',
  '0190ac0e-1a01-7001-8a01-000000000303',
] as const

// Cross-tenant audit lookup fixture (5.2). One Acme + one Beta trace, each carrying its
// tenant. The richer 5.2 search (by source/stage/time) is a flagged seam, not built.
const CROSS_TENANT_AUDIT: Record<string, CrossTenantAuditTrace> = {
  [QUARANTINE_TRACE_IDS.acmeCanonical]: {
    trace_id: QUARANTINE_TRACE_IDS.acmeCanonical,
    tenant_id: TENANTS.acme.id,
    tenant_name: TENANTS.acme.name,
    source_id: 'manual_csv_upload',
    stages: [
      { stage: 'received', at: '2026-06-03T09:08:00Z', status: 'ok' },
      { stage: 'validated', at: '2026-06-03T09:08:01Z', status: 'ok' },
      { stage: 'mapped', at: '2026-06-03T09:08:02Z', status: 'ok', mapping_version_id: 1 },
      { stage: 'quarantined', at: '2026-06-03T09:08:03Z', status: 'error', error_code: 'CANONICAL_SHAPE_INVALID' },
    ],
    prior_trace_id: null,
  },
  [BETA_AUDIT_TRACE]: {
    trace_id: BETA_AUDIT_TRACE,
    tenant_id: TENANTS.beta.id,
    tenant_name: TENANTS.beta.name,
    source_id: 'square_pos_v1',
    stages: [
      { stage: 'received', at: '2026-06-04T07:40:00Z', status: 'ok' },
      { stage: 'validated', at: '2026-06-04T07:40:01Z', status: 'ok' },
      { stage: 'mapped', at: '2026-06-04T07:40:02Z', status: 'ok', mapping_version_id: 2 },
      { stage: 'committed', at: '2026-06-04T07:40:03Z', status: 'ok' },
    ],
    prior_trace_id: null,
  },
}

function resubmitsFor(traceId: string): ResubmitRecord[] {
  return opsResubmitStore[traceId] ?? []
}

function ensureFixtureMode(fn: string): void {
  if (SERVER_MODE === 'real') {
    throw new Error(`real-mode ${fn} is not implemented (slice 13)`)
  }
}

export async function getFleetQuarantine(): Promise<FleetQuarantineRow[]> {
  ensureFixtureMode('getFleetQuarantine()')
  return FLEET_QUARANTINE
}

export async function getFleetQuarantineRow(traceId: string): Promise<QuarantineDetail> {
  ensureFixtureMode('getFleetQuarantineRow()')
  const base = FLEET_DETAIL[traceId]
  if (base === undefined) {
    throw new Error(`no fixture fleet quarantine detail for trace_id ${traceId}`)
  }
  const records = resubmitsFor(traceId)
  return { ...base, chain_depth: base.chain_depth + records.length, resubmits: records }
}

// Tenant-context resubmit (4.3 + tenant_id). Cap-enforced via CHAIN_DEPTH_CAP, mirroring
// the tenant-mode postResubmit; the row's tenant_id rides in the request only.
export async function postOpsResubmit(req: OpsResubmitRequest): Promise<ResubmitResponse> {
  ensureFixtureMode('postOpsResubmit()')
  const base = FLEET_DETAIL[req.parent_trace_id]
  if (base === undefined) {
    throw new Error(`no fixture fleet quarantine detail for trace_id ${req.parent_trace_id}`)
  }
  const records = resubmitsFor(req.parent_trace_id)
  const currentDepth = base.chain_depth + records.length
  if (currentDepth >= CHAIN_DEPTH_CAP) {
    throw new Error(
      `resubmit rejected for trace_id ${req.parent_trace_id}: chain depth ${currentDepth} at cap ${CHAIN_DEPTH_CAP} (architecture 6.5)`,
    )
  }
  const newDepth = currentDepth + 1
  const childTraceId = OPS_RESUBMIT_CHILD_TRACE_IDS[records.length]
  opsResubmitStore[req.parent_trace_id] = [
    ...records,
    { child_trace_id: childTraceId, resubmit_type: req.resubmit_type, chain_depth: newDepth },
  ]
  return {
    trace_id: childTraceId,
    parent_trace_id: req.parent_trace_id,
    resubmit_type: req.resubmit_type,
    chain_depth: newDepth,
    status: 'accepted',
  }
}

// Cross-tenant trace lookup (5.2). NO tenant filter (the tenant-mode getAuditTrace filters
// by snapshot.tenantId, which is null for an ops user). Returns null for an unknown trace.
export async function getCrossTenantAuditTrace(traceId: string): Promise<CrossTenantAuditTrace | null> {
  ensureFixtureMode('getCrossTenantAuditTrace()')
  return CROSS_TENANT_AUDIT[traceId] ?? null
}

const FLEET_QUARANTINE_KEY = ['dis-ui-server', 'ops-cross-tenant', 'quarantine'] as const

export function useFleetQuarantine(enabled: boolean) {
  return useQuery({
    queryKey: [...FLEET_QUARANTINE_KEY, 'list'],
    queryFn: () => getFleetQuarantine(),
    enabled,
    staleTime: Infinity,
    retry: false,
  })
}

export function useFleetQuarantineRow(traceId: string | null) {
  return useQuery({
    queryKey: [...FLEET_QUARANTINE_KEY, 'detail', traceId ?? 'none'],
    queryFn: () => getFleetQuarantineRow(traceId as string),
    enabled: traceId !== null,
    staleTime: Infinity,
    retry: false,
  })
}

export function useOpsResubmit() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (req: OpsResubmitRequest) => postOpsResubmit(req),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: FLEET_QUARANTINE_KEY }),
  })
}

export function useCrossTenantAuditTrace(traceId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['dis-ui-server', 'ops-cross-tenant', 'audit', traceId ?? 'none'],
    queryFn: () => getCrossTenantAuditTrace(traceId as string),
    enabled: enabled && traceId !== null,
    staleTime: Infinity,
    retry: false,
  })
}
