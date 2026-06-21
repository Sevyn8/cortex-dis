import { useQuery } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { getJson } from './client'
import { isRealMode } from './mode'

// Tenant Quarantine console (slice 15a, b8b85f4), tenant slice. Types mirror the real
// dis-ui-server contract EXACTLY (services/dis-ui-server/.../schemas/quarantine.py): the two
// reads are GET /api/v1/quarantine?source=&error_type=&status=&window= -> { items, open_count }
// (open_count is filter-INDEPENDENT) and GET /api/v1/quarantine/{item_id} where item_id is the
// type-tagged "row:<uuid>"/"chunk:<uuid>" handle returned by the list (round-tripped verbatim).
// Mode-aware (T10): real mode calls the live endpoints; fixture mode (default + tests) returns
// inlined items and applies the filters client-side so the screen works with no backend.
//
// Honest semantics (slice 15a): original_payload is DEFERRED -> ALWAYS null; status "resolved"
// returns nothing (no resolve path exists, D82); source == source_id (no registry); chain_depth
// is always 0 (no lineage). There is NO resolve/dismiss/resubmit action server-side.

// Wire vocabularies (mirrored 1:1 from the backend Literal types).
export type Kind = 'row' | 'chunk'
export type StatusWire = 'open' | 'resolved'
export type WindowWire = '24h' | '7d' | '30d'
export type StageWire = 'source-shape' | 'canonical-shape' | 'fk' | 'normalization' | 'other'

export type QuarantineListRow = {
  id: string // type-tagged "row:<uuid>"/"chunk:<uuid>" - opaque, round-tripped to detail
  kind: Kind
  trace_id: string
  source_id: string // the filter key (Dashboard ?source= deep link)
  source: string // display name; == source_id today
  error_reason: string // a FailureCode member
  failure_stage: StageWire
  failed_at: string // ISO-8601
  status: StatusWire
}

export type QuarantineListResponse = {
  items: QuarantineListRow[]
  open_count: number // filter-INDEPENDENT (the header badge)
}

export type QuarantineDetail = {
  id: string
  kind: Kind
  trace_id: string
  source: string
  failed_at: string
  mapping_version: number | null // the "v1" token; null for pre-lookup chunk failures
  error_reason: string
  failure_stage: StageWire
  error_context: string
  original_payload: Record<string, unknown> | null // DEFERRED this slice -> always null
  chain_depth: number // always 0 (no lineage until Slice 12)
}

// The four server-side filters (all optional; absent = no constraint). Mirrors the query params.
export type QuarantineFilters = {
  source?: string
  errorType?: StageWire
  status?: StatusWire
  window?: WindowWire
}

// ---- Fixture data (local dev + tests). Two sources so the Source filter has options; all open
// (resolved yields nothing, matching the real endpoint). original_payload is always null. ----

const FIXTURE_ROWS: QuarantineListRow[] = [
  {
    id: 'row:0190ac0e-1a01-7001-8a01-000000000001',
    kind: 'row',
    trace_id: '0190ac0e-1a01-7001-8a01-000000000001',
    source_id: 'manual_csv_upload',
    source: 'manual_csv_upload',
    error_reason: 'POST_VALIDATION_FAILED',
    failure_stage: 'canonical-shape',
    failed_at: '2026-06-09T09:08:00Z',
    status: 'open',
  },
  {
    id: 'row:0190ac0e-1a01-7001-8a01-000000000002',
    kind: 'row',
    trace_id: '0190ac0e-1a01-7001-8a01-000000000002',
    source_id: 'manual_csv_upload',
    source: 'manual_csv_upload',
    error_reason: 'MAPPING_EXECUTION_FAILED',
    failure_stage: 'normalization',
    failed_at: '2026-06-09T09:05:00Z',
    status: 'open',
  },
  {
    id: 'chunk:0190ac0e-1a01-7001-8a01-000000000003',
    kind: 'chunk',
    trace_id: '0190ac0e-1a01-7001-8a01-000000000003',
    source_id: 'shopify_pos_v2',
    source: 'shopify_pos_v2',
    error_reason: 'PRE_VALIDATION_FAILED',
    failure_stage: 'source-shape',
    failed_at: '2026-06-08T16:40:00Z',
    status: 'open',
  },
]

const FIXTURE_DETAILS: Record<string, QuarantineDetail> = {
  'row:0190ac0e-1a01-7001-8a01-000000000001': {
    id: 'row:0190ac0e-1a01-7001-8a01-000000000001',
    kind: 'row',
    trace_id: '0190ac0e-1a01-7001-8a01-000000000001',
    source: 'manual_csv_upload',
    failed_at: '2026-06-09T09:08:00Z',
    mapping_version: 1,
    error_reason: 'POST_VALIDATION_FAILED',
    failure_stage: 'canonical-shape',
    error_context: 'canonical-shape: column price failed numeric cast',
    original_payload: null,
    chain_depth: 0,
  },
  'row:0190ac0e-1a01-7001-8a01-000000000002': {
    id: 'row:0190ac0e-1a01-7001-8a01-000000000002',
    kind: 'row',
    trace_id: '0190ac0e-1a01-7001-8a01-000000000002',
    source: 'manual_csv_upload',
    failed_at: '2026-06-09T09:05:00Z',
    mapping_version: 1,
    error_reason: 'MAPPING_EXECUTION_FAILED',
    failure_stage: 'normalization',
    error_context: 'normalization: column sold_at unparseable (expected ISO-8601)',
    original_payload: null,
    chain_depth: 0,
  },
  'chunk:0190ac0e-1a01-7001-8a01-000000000003': {
    id: 'chunk:0190ac0e-1a01-7001-8a01-000000000003',
    kind: 'chunk',
    trace_id: '0190ac0e-1a01-7001-8a01-000000000003',
    source: 'shopify_pos_v2',
    failed_at: '2026-06-08T16:40:00Z',
    mapping_version: null, // pre-lookup chunk failure carries no mapping version
    error_reason: 'PRE_VALIDATION_FAILED',
    failure_stage: 'source-shape',
    error_context: 'source-shape: required column sku absent from payload',
    original_payload: null,
    chain_depth: 0,
  },
}

const WINDOW_MS: Record<WindowWire, number> = {
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
}

// Fixture-mode filtering, mirroring the server's WHERE: source (source_id), error_type
// (failure_stage), status (open == the only producing state; resolved yields nothing), and the
// trailing time window. open_count is the count of open items, INDEPENDENT of the filters.
function fixtureList(filters: QuarantineFilters): QuarantineListResponse {
  const now = Date.now()
  const items = FIXTURE_ROWS.filter(
    (r) =>
      (filters.source === undefined || r.source_id === filters.source) &&
      (filters.errorType === undefined || r.failure_stage === filters.errorType) &&
      (filters.status === undefined || r.status === filters.status) &&
      (filters.window === undefined ||
        now - new Date(r.failed_at).getTime() <= WINDOW_MS[filters.window]),
  )
  return { items, open_count: FIXTURE_ROWS.filter((r) => r.status === 'open').length }
}

function buildQuery(filters: QuarantineFilters): string {
  const params = new URLSearchParams()
  if (filters.source !== undefined) params.set('source', filters.source)
  if (filters.errorType !== undefined) params.set('error_type', filters.errorType)
  if (filters.status !== undefined) params.set('status', filters.status)
  if (filters.window !== undefined) params.set('window', filters.window)
  const q = params.toString()
  return q.length > 0 ? `?${q}` : ''
}

// GET /api/v1/quarantine. Tenant-scoped server-side (token tenant only).
export async function getQuarantineList(
  filters: QuarantineFilters,
): Promise<QuarantineListResponse> {
  if (isRealMode()) {
    return getJson<QuarantineListResponse>(`/api/v1/quarantine${buildQuery(filters)}`)
  }
  return fixtureList(filters)
}

// GET /api/v1/quarantine/{item_id}. item_id is the type-tagged handle from the list row, sent
// verbatim (getting the "row:"/"chunk:" prefix wrong breaks detail dispatch server-side).
export async function getQuarantineDetail(itemId: string): Promise<QuarantineDetail> {
  if (isRealMode()) {
    return getJson<QuarantineDetail>(`/api/v1/quarantine/${encodeURIComponent(itemId)}`)
  }
  const detail = FIXTURE_DETAILS[itemId]
  if (detail === undefined) {
    throw new Error(`no fixture quarantine detail for item id ${itemId}`)
  }
  return detail
}

export function useQuarantineList(snapshot: AuthSnapshot | null, filters: QuarantineFilters) {
  return useQuery({
    queryKey: [
      'dis-ui-server',
      'quarantine',
      'list',
      snapshot?.tenantId ?? 'none',
      filters.source ?? 'all',
      filters.errorType ?? 'all',
      filters.status ?? 'all',
      filters.window ?? 'all',
    ],
    queryFn: () => getQuarantineList(filters),
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}

export function useQuarantineDetail(snapshot: AuthSnapshot | null, itemId: string | null) {
  return useQuery({
    queryKey: ['dis-ui-server', 'quarantine', 'detail', itemId ?? 'none'],
    queryFn: () => getQuarantineDetail(itemId as string),
    enabled: snapshot !== null && itemId !== null,
    staleTime: Infinity,
    retry: false,
  })
}
