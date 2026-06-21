import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import {
  getActiveVersion,
  getStagedVersion,
  promoteStagedVersion,
  rejectStagedVersion,
} from './mappings'
import { SERVER_MODE } from './mode'

// Shadow Rollout Review endpoints (demand list 2.6-2.9), tenant slice. Fixture mode
// (default); real mode is OPEN (slice 13) and throws. Slice 22 builds these on OPEN
// contracts, so every shape here is PROVISIONAL and lives only in this fixture layer
// (the single reconciliation point); the screen consumes typed values only.
//
// What the demand list pins is prose, not schemas:
//   2.6 shadow-stats: "window, input chunks, staged rows, validation pass rate,
//       diff-vs-active counts" - no schema.
//   2.7 shadow-diff (query `limit`): "sample diff rows (staged vs active)" - no row
//       shape; modeled on the surface-map wireframe. Diff columns use the reconciled
//       canonical vocabulary (source_sale_timestamp), NOT the wireframe's stale
//       event_ts (D40 / slices 03-04).
//   2.8 promote / 2.9 reject: no response shape; the promote side effect
//       (mapping.changed publish, D6/D22) is NOT modeled here (no event bus in the
//       fixture). We model only the version-state transition the UI observes.

// 2.6 rollup. PROVISIONAL.
export type ShadowStats = {
  source_id: string
  staged_version: number
  active_version: number | null
  window: string
  input_chunks: number
  staged_rows: number
  validation_pass_rate: number
  validation_fail_count: number
  diff_identical: number
  diff_differing: number
  diff_column: string
}

// 2.7 sample diff row. PROVISIONAL.
export type ShadowDiffRow = {
  sku_id: string
  column: string
  active_value: string
  staged_value: string
}

// 2.8 / 2.9 results. PROVISIONAL.
export type PromoteResult = {
  source_id: string
  promoted_version: number
  deprecated_version: number | null
  status: 'promoted'
}

export type RejectResult = {
  source_id: string
  rejected_version: number
  status: 'rejected'
}

// Provisional rollup constants per source (the staged/active version numbers are
// read live from the mappings store so they stay coherent after a promote). The
// diff sample concentrates on source_sale_timestamp (a date-format normalization),
// matching the v3 staged mapping's normalize rule.
const SHADOW_ROLLUP: Record<string, Omit<ShadowStats, 'source_id' | 'staged_version' | 'active_version'>> = {
  manual_csv_upload: {
    window: 'last 48h',
    input_chunks: 3124,
    staged_rows: 18402,
    validation_pass_rate: 0.994,
    validation_fail_count: 96,
    diff_identical: 12011,
    diff_differing: 6391,
    diff_column: 'source_sale_timestamp',
  },
}

const SHADOW_DIFF_SAMPLE: Record<string, ShadowDiffRow[]> = {
  manual_csv_upload: [
    { sku_id: 'A123', column: 'source_sale_timestamp', active_value: '2026-03-12', staged_value: '2026-12-03' },
    { sku_id: 'B456', column: 'source_sale_timestamp', active_value: '2026-03-08', staged_value: '2026-08-03' },
    { sku_id: 'C789', column: 'source_sale_timestamp', active_value: '2026-02-01', staged_value: '2026-01-02' },
  ],
}

function ensureFixtureMode(fn: string): void {
  if (SERVER_MODE === 'real') {
    throw new Error(`real-mode ${fn} is not implemented (slice 13)`)
  }
}

// Null when the source has no staged version (drives the empty state, FM4).
export async function getShadowStats(
  snapshot: AuthSnapshot,
  sourceId: string,
): Promise<ShadowStats | null> {
  ensureFixtureMode('getShadowStats()')
  const staged = getStagedVersion(snapshot, sourceId)
  const rollup = SHADOW_ROLLUP[sourceId]
  if (staged === null || rollup === undefined) {
    return null
  }
  // active_version is read live; null on first onboarding (no prior active).
  return {
    source_id: sourceId,
    staged_version: staged.version,
    active_version: getActiveVersion(snapshot, sourceId)?.version ?? null,
    ...rollup,
  }
}

// Sample diff rows. Empty when no staged version, or no prior active (2.6 note:
// first onboarding shows no diff, only the pass rate).
export async function getShadowDiff(
  snapshot: AuthSnapshot,
  sourceId: string,
): Promise<ShadowDiffRow[]> {
  ensureFixtureMode('getShadowDiff()')
  const staged = getStagedVersion(snapshot, sourceId)
  if (staged === null || getActiveVersion(snapshot, sourceId) === null) {
    return []
  }
  return SHADOW_DIFF_SAMPLE[sourceId] ?? []
}

export async function promoteShadow(
  snapshot: AuthSnapshot,
  sourceId: string,
): Promise<PromoteResult> {
  ensureFixtureMode('promoteShadow()')
  const { promoted, deprecated } = promoteStagedVersion(snapshot, sourceId)
  return { source_id: sourceId, promoted_version: promoted, deprecated_version: deprecated, status: 'promoted' }
}

export async function rejectShadow(snapshot: AuthSnapshot, sourceId: string): Promise<RejectResult> {
  ensureFixtureMode('rejectShadow()')
  const { rejected } = rejectStagedVersion(snapshot, sourceId)
  return { source_id: sourceId, rejected_version: rejected, status: 'rejected' }
}

const SHADOW_KEY = ['dis-ui-server', 'shadow'] as const
const MAPPINGS_KEY = ['dis-ui-server', 'mappings'] as const

export function useShadowStats(snapshot: AuthSnapshot | null, sourceId: string | null) {
  return useQuery({
    queryKey: [...SHADOW_KEY, snapshot?.tenantId ?? 'none', sourceId ?? 'none', 'stats'],
    queryFn: () => getShadowStats(snapshot as AuthSnapshot, sourceId as string),
    enabled: snapshot !== null && sourceId !== null,
    staleTime: Infinity,
    retry: false,
  })
}

export function useShadowDiff(snapshot: AuthSnapshot | null, sourceId: string | null) {
  return useQuery({
    queryKey: [...SHADOW_KEY, snapshot?.tenantId ?? 'none', sourceId ?? 'none', 'diff'],
    queryFn: () => getShadowDiff(snapshot as AuthSnapshot, sourceId as string),
    enabled: snapshot !== null && sourceId !== null,
    staleTime: Infinity,
    retry: false,
  })
}

// Promote/reject invalidate BOTH the shadow prefix (the stats/diff for this screen)
// and the mappings prefix (so the Mapping Versions screen reflects the transition,
// FM6).
export function usePromoteShadow(snapshot: AuthSnapshot | null, sourceId: string | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => promoteShadow(snapshot as AuthSnapshot, sourceId as string),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SHADOW_KEY })
      void queryClient.invalidateQueries({ queryKey: MAPPINGS_KEY })
    },
  })
}

export function useRejectShadow(snapshot: AuthSnapshot | null, sourceId: string | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => rejectShadow(snapshot as AuthSnapshot, sourceId as string),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SHADOW_KEY })
      void queryClient.invalidateQueries({ queryKey: MAPPINGS_KEY })
    },
  })
}
