import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { SERVER_MODE } from './mode'

// Quarantine endpoints (demand list 4.1/4.2), tenant slice. Fixture mode (default)
// returns the inlined fixtures; real mode is OPEN (slice 13) and throws, mirroring
// sources.ts / me.ts. Shapes are PROVISIONAL pending Sanjeev's slices 15-17.

// PROVISIONAL enum (demand list 4.1).
export type FailureStage = 'source-shape' | 'canonical-shape' | 'fk' | 'normalization'
export type QuarantineStatus = 'open' | 'resolved'

// 4.1 list row. `source` is the DISPLAY string; `source_id` is the kind-style composite
// key the UI keys the source filter on (and that the Dashboard ?source= link carries).
// source identity is the (tenant_id, source_id) composite.
export type QuarantineRow = {
  trace_id: string
  source_id: string
  source: string
  store: string
  error_reason: string
  failure_stage: FailureStage
  mapping_version: number
  failed_at: string
  status: QuarantineStatus
}

// 4.2 detail. PROVISIONAL: the demand list names the fields (original payload,
// error context, mapping version, chain depth) but gives NO shape. This struct
// and the original_payload contents are an invented placeholder, not canonical.
// `resubmits` is the recorded resubmit history (overlaid from the mutable store,
// below) so the screen reflects post-resubmit state without re-deriving it.
export type QuarantineDetail = {
  trace_id: string
  source: string
  store: string
  failed_at: string
  failure_stage: FailureStage
  mapping_version: number
  error_reason: string
  error_context: string
  original_payload: Record<string, unknown>
  chain_depth: number
  resubmits: ResubmitRecord[]
}

// Resubmit action (demand list 4.3). The request body is PINNED by 4.3:
// { resubmit_type, parent_trace_id }. Everything else here is PROVISIONAL and lives
// only in this fixture layer (slice 22 containment), so reconciliation against the
// real contract is a single edit, not a screen rewrite.
export type ResubmitType = 'replay' | 'fixed_file'

// 4.3 body - PINNED. parent_trace_id is the trace being resubmitted.
export type ResubmitRequest = {
  resubmit_type: ResubmitType
  parent_trace_id: string
}

// PROVISIONAL: 4.3 gives NO response shape. This models the publisher minting a new
// child trace_id and the incremented chain_depth (architecture 6.5). Flagged for
// reconciliation. NOTE (surfaced): 4.3's body carries no file/upload reference, yet
// arch 6.5's fixed_file uploads a corrected file (fresh bronze) - the real fixed_file
// flow likely needs an upload ref 4.3 omits; we do NOT invent one here.
export type ResubmitResponse = {
  trace_id: string
  parent_trace_id: string
  resubmit_type: ResubmitType
  chain_depth: number
  status: 'accepted'
}

// One recorded resubmit (the minted child and its depth).
export type ResubmitRecord = {
  child_trace_id: string
  resubmit_type: ResubmitType
  chain_depth: number
}

// Chain-depth cap (architecture 6.5): resubmits beyond depth 3 are rejected by the
// publisher; over-cap chunks become an ops escalation.
export const CHAIN_DEPTH_CAP = 3

// Deterministic UUIDv7-shaped child trace ids (no Date.now / Math.random, which are
// unavailable here). Continues the QUARANTINE_TRACE_IDS numbering (version nibble 7,
// variant 8). A depth-0 chain can resubmit at most CHAIN_DEPTH_CAP times, so three
// ids suffice. PROVISIONAL: the real backend mints these (4.3 / arch 6.5).
const RESUBMIT_CHILD_TRACE_IDS = [
  '0190ac0e-1a01-7001-8a01-000000000101',
  '0190ac0e-1a01-7001-8a01-000000000102',
  '0190ac0e-1a01-7001-8a01-000000000103',
] as const

// trace_ids are UUIDv7 (dis-core trace_id.py: `trace_id uuid NOT NULL`, minted via
// new_uuid7). These are synthetic UUIDv7-shaped fixtures (version nibble 7, variant
// 8); the final segment preserves the prior numbering for traceability. The
// `acmeCanonical` id is cross-referenced by the Audit fixture (audit.ts imports it),
// so the same trace appears in both the Quarantine and Audit screens.
export const QUARANTINE_TRACE_IDS = {
  acmeCanonical: '0190ac0e-1a01-7001-8a01-000000000001',
  acmeNormalization: '0190ac0e-1a01-7001-8a01-000000000002',
  shopifySourceShape: '0190ac0e-1a01-7001-8a01-000000000003',
  shopifyFk: '0190ac0e-1a01-7001-8a01-000000000004',
  acmeResolved: '0190ac0e-1a01-7001-8a01-000000000005',
} as const

// Fixtures for the primary tenant. Sources: `manual_csv_upload` is the real seeded
// source (display "Manual CSV Upload"); `shopify_pos_v2` is a schema-example
// kind-style source_id (display "Shopify POS"), NOT an invented opaque id - added
// only so the source filter has more than one option.
const QUARANTINE_FIXTURES: Record<string, QuarantineRow[]> = {
  t_acme9k2l1mn4: [
    {
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
      trace_id: QUARANTINE_TRACE_IDS.acmeNormalization,
      source_id: 'manual_csv_upload',
      source: 'Manual CSV Upload',
      store: 'Acme Downtown #1',
      error_reason: 'bad date format in event_ts',
      failure_stage: 'normalization',
      mapping_version: 1,
      failed_at: '2026-06-03T09:05:00Z',
      status: 'open',
    },
    {
      trace_id: QUARANTINE_TRACE_IDS.shopifySourceShape,
      source_id: 'shopify_pos_v2',
      source: 'Shopify POS',
      store: 'Acme Downtown #1',
      error_reason: 'missing required column sku',
      failure_stage: 'source-shape',
      mapping_version: 2,
      failed_at: '2026-06-02T16:40:00Z',
      status: 'open',
    },
    {
      trace_id: QUARANTINE_TRACE_IDS.shopifyFk,
      source_id: 'shopify_pos_v2',
      source: 'Shopify POS',
      store: 'Acme Downtown #1',
      error_reason: 'store_id not found in identity_mirror',
      failure_stage: 'fk',
      mapping_version: 2,
      failed_at: '2026-05-28T11:00:00Z',
      status: 'resolved',
    },
    {
      trace_id: QUARANTINE_TRACE_IDS.acmeResolved,
      source_id: 'manual_csv_upload',
      source: 'Manual CSV Upload',
      store: 'Acme Downtown #1',
      error_reason: "price '-' not a valid number",
      failure_stage: 'canonical-shape',
      mapping_version: 1,
      failed_at: '2026-05-20T08:30:00Z',
      status: 'resolved',
    },
  ],
}

// Stored base detail (the runtime `resubmits` history and the effective chain_depth
// are overlaid by getQuarantineRow from the mutable store below). `chain_depth` here
// is the BASE depth: 0 for a never-resubmitted row, 3 for a row already at the cap.
type QuarantineDetailBase = Omit<QuarantineDetail, 'resubmits'>

const QUARANTINE_DETAIL_FIXTURES: Record<string, QuarantineDetailBase> = {
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
  [QUARANTINE_TRACE_IDS.shopifySourceShape]: {
    trace_id: QUARANTINE_TRACE_IDS.shopifySourceShape,
    source: 'Shopify POS',
    store: 'Acme Downtown #1',
    failed_at: '2026-06-02T16:40:00Z',
    failure_stage: 'source-shape',
    mapping_version: 2,
    error_reason: 'missing required column sku',
    error_context: 'source-shape validation: required column sku absent from payload',
    original_payload: { item: 'B456', price: '9.99', qty: '1' },
    chain_depth: 0,
  },
  // Already at the chain-depth cap (arch 6.5): the Resubmit action is disabled with
  // its reason. Exercises AC3 against a real row.
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
}

// Mutable resubmit store (notifications.ts pattern), keyed by parent trace_id. Each
// resubmit appends a record; effective depth = base chain_depth + records.length.
let resubmitStore: Record<string, ResubmitRecord[]> = {}

// Test-only: clear recorded resubmits so mutations do not bleed between tests.
export function __resetQuarantineFixture(): void {
  resubmitStore = {}
}

function resubmitsFor(traceId: string): ResubmitRecord[] {
  return resubmitStore[traceId] ?? []
}

export async function getQuarantine(snapshot: AuthSnapshot): Promise<QuarantineRow[]> {
  if (SERVER_MODE === 'real') {
    throw new Error('real-mode getQuarantine() is not implemented (slice 13)')
  }
  return QUARANTINE_FIXTURES[snapshot.tenantId ?? ''] ?? []
}

export async function getQuarantineRow(traceId: string): Promise<QuarantineDetail> {
  if (SERVER_MODE === 'real') {
    throw new Error('real-mode getQuarantineRow() is not implemented (slice 13)')
  }
  const base = QUARANTINE_DETAIL_FIXTURES[traceId]
  if (base === undefined) {
    throw new Error(`no fixture quarantine detail for trace_id ${traceId}`)
  }
  // Overlay the recorded resubmits: effective depth grows with each resubmit, and the
  // history rides along so the screen can show the new state after invalidation.
  const records = resubmitsFor(traceId)
  return { ...base, chain_depth: base.chain_depth + records.length, resubmits: records }
}

// POST /quarantine/{trace_id}/resubmit (demand list 4.3). The argument IS the 4.3
// body, so the wire shape is explicit at the call site. Records the resubmit in the
// mutable store and returns the (PROVISIONAL) response. Enforces the chain-depth cap
// defensively - the UI disables the action at the cap, this guards the data path.
export async function postResubmit(req: ResubmitRequest): Promise<ResubmitResponse> {
  if (SERVER_MODE === 'real') {
    throw new Error('real-mode postResubmit() is not implemented (slice 13)')
  }
  const base = QUARANTINE_DETAIL_FIXTURES[req.parent_trace_id]
  if (base === undefined) {
    throw new Error(`no fixture quarantine detail for trace_id ${req.parent_trace_id}`)
  }
  const records = resubmitsFor(req.parent_trace_id)
  const currentDepth = base.chain_depth + records.length
  if (currentDepth >= CHAIN_DEPTH_CAP) {
    throw new Error(
      `resubmit rejected for trace_id ${req.parent_trace_id}: chain depth ${currentDepth} at cap ${CHAIN_DEPTH_CAP} (architecture 6.5)`,
    )
  }
  const newDepth = currentDepth + 1
  const childTraceId = RESUBMIT_CHILD_TRACE_IDS[records.length]
  const record: ResubmitRecord = {
    child_trace_id: childTraceId,
    resubmit_type: req.resubmit_type,
    chain_depth: newDepth,
  }
  resubmitStore[req.parent_trace_id] = [...records, record]
  return {
    trace_id: childTraceId,
    parent_trace_id: req.parent_trace_id,
    resubmit_type: req.resubmit_type,
    chain_depth: newDepth,
    status: 'accepted',
  }
}

export function useQuarantine(snapshot: AuthSnapshot | null) {
  return useQuery({
    queryKey: ['dis-ui-server', 'quarantine', snapshot?.tenantId ?? 'none'],
    queryFn: () => getQuarantine(snapshot as AuthSnapshot),
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}

export function useQuarantineRow(traceId: string | null) {
  return useQuery({
    queryKey: ['dis-ui-server', 'quarantine', 'detail', traceId ?? 'none'],
    queryFn: () => getQuarantineRow(traceId as string),
    enabled: traceId !== null,
    staleTime: Infinity,
    retry: false,
  })
}

// Resubmit mutation (4.3). onSuccess invalidates the shared ['dis-ui-server',
// 'quarantine'] prefix, which matches BOTH the list key and the detail key, so the
// list and the open row detail refetch and reflect the new chain depth.
export function useResubmit() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (req: ResubmitRequest) => postResubmit(req),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dis-ui-server', 'quarantine'] }),
  })
}
