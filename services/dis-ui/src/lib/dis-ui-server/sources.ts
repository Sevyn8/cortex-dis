import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { SERVER_MODE } from './mode'

export type SourceStatus = 'active' | 'staged' | 'deprecated' | 'failing'

// Shape per demand list 1.3. The server response is RLS-scoped to the caller's
// tenant (no tenant_id field on the wire); the client fixtures below are keyed by
// tenant for scoping.
export type Source = {
  source_id: string
  name: string
  type: string
  store: string
  status: SourceStatus
  active_version: number
  quarantine_rate_24h: number
  last_ok_at: string
}

// The UI-defined source-create contract (slice 27), PROVISIONAL. ONE shape for BOTH the
// explicit Sources CRUD create AND the onboarding "attach to new source" path (FM2). The
// source RECORD SCHEMA - whether name/type/store are real config.source_mappings columns
// or UI display metadata - and WHERE source registration lives are Sanjeev's to confirm;
// the UI proposes the API shape it needs, it does not assert the schema (FM3, D37 open).
export type SourceDraft = {
  source_id: string // kind-style key (e.g. manual_csv_upload); set once, never re-keyed (FM4)
  name: string
  type: string
  store: string
}

// Provisional source-type vocabulary (the kind of feed). Not schema-confirmed.
export const SOURCE_TYPES = ['CSV', 'JSON', 'API'] as const

// source_id is a kind-style slug, NOT an opaque per-instance id (D37 open). The CRUD
// form lets the operator set it; the onboarding attach-to-new path derives it from the
// entered name/kind. Same rule, two entry points.
export function deriveSourceId(input: string): string {
  return input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

// Normalize/default a partial into the one SourceDraft shape. Both the CRUD create form
// and the onboarding attach-to-new build their draft through this, so the two paths
// define a source identically (FM2).
export function makeSourceDraft(input: Partial<SourceDraft>): SourceDraft {
  return {
    source_id: input.source_id ?? '',
    name: input.name ?? '',
    type: input.type ?? 'CSV',
    store: input.store ?? '',
  }
}

// SEED keyed by tenant id. GROUNDED on Sanjeev's slice-2 seed (fixtures.py): the primary
// tenant t_acme9k2l1mn4 has one ACTIVE config.source_mappings row, source_id
// "manual_csv_upload", store "Acme Downtown #1". name / type / quarantine_rate_24h /
// last_ok_at are PROVISIONAL display values. CRUD (slice 27) mutates a clone of this SEED.
const SOURCE_SEED: Record<string, Source[]> = {
  t_acme9k2l1mn4: [
    {
      source_id: 'manual_csv_upload',
      name: 'Manual CSV Upload',
      type: 'CSV',
      store: 'Acme Downtown #1',
      status: 'active',
      active_version: 1,
      quarantine_rate_24h: 0,
      last_ok_at: '2026-06-03T09:12:00Z',
    },
  ],
}

function cloneSeed(): Record<string, Source[]> {
  return Object.fromEntries(
    Object.entries(SOURCE_SEED).map(([tenant, list]) => [tenant, list.map((s) => ({ ...s }))]),
  )
}

// Mutable store (notifications.ts pattern): create/edit/deprecate transition source state.
let store = cloneSeed()

// Test-only: restore the SEED so CRUD mutations do not bleed between tests.
export function __resetSourcesFixture(): void {
  store = cloneSeed()
}

function tenantSources(snapshot: AuthSnapshot): Source[] {
  return store[snapshot.tenantId ?? ''] ?? (store[snapshot.tenantId ?? ''] = [])
}

function ensureFixtureMode(fn: string): void {
  if (SERVER_MODE === 'real') {
    throw new Error(`real-mode ${fn} is not implemented (slice 13)`)
  }
}

// Lists the caller's sources (tenant-scoped). Fixture mode (default) reads the store; real
// mode is OPEN (slice 13) and throws rather than guessing the wire call.
export async function getSources(snapshot: AuthSnapshot): Promise<Source[]> {
  ensureFixtureMode('getSources()')
  // Return a copy so callers never hold (and accidentally mutate) the live store array.
  return [...(store[snapshot.tenantId ?? ''] ?? [])]
}

export async function getSource(snapshot: AuthSnapshot, sourceId: string): Promise<Source | null> {
  ensureFixtureMode('getSource()')
  return tenantSources(snapshot).find((s) => s.source_id === sourceId) ?? null
}

// Create (slice 27). Appends a source from the shared SourceDraft. A freshly created
// source is active with no mapping yet (active_version 0).
export async function createSource(snapshot: AuthSnapshot, draft: SourceDraft): Promise<Source> {
  ensureFixtureMode('createSource()')
  const created: Source = {
    source_id: draft.source_id,
    name: draft.name,
    type: draft.type,
    store: draft.store,
    status: 'active',
    active_version: 0,
    quarantine_rate_24h: 0,
    last_ok_at: '',
  }
  tenantSources(snapshot).push(created)
  return created
}

// Edit (slice 27). Updates display metadata ONLY; source_id is the immutable key (FM4).
export async function updateSource(
  snapshot: AuthSnapshot,
  sourceId: string,
  patch: Pick<SourceDraft, 'name' | 'type' | 'store'>,
): Promise<Source> {
  ensureFixtureMode('updateSource()')
  const source = tenantSources(snapshot).find((s) => s.source_id === sourceId)
  if (source === undefined) {
    throw new Error(`no fixture source for source_id ${sourceId}`)
  }
  source.name = patch.name
  source.type = patch.type
  source.store = patch.store
  return source
}

// Deprecate (slice 27). Soft status transition active -> deprecated. There is NO hard
// delete: a source has canonical/audit data behind it (FM1).
export async function deprecateSource(snapshot: AuthSnapshot, sourceId: string): Promise<Source> {
  ensureFixtureMode('deprecateSource()')
  const source = tenantSources(snapshot).find((s) => s.source_id === sourceId)
  if (source === undefined) {
    throw new Error(`no fixture source for source_id ${sourceId}`)
  }
  source.status = 'deprecated'
  return source
}

const SOURCES_KEY = ['dis-ui-server', 'sources'] as const

export function useSources(snapshot: AuthSnapshot | null) {
  return useQuery({
    queryKey: [...SOURCES_KEY, snapshot?.tenantId ?? 'none'],
    queryFn: () => getSources(snapshot as AuthSnapshot),
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}

export function useSource(snapshot: AuthSnapshot | null, sourceId: string | null) {
  return useQuery({
    queryKey: [...SOURCES_KEY, 'detail', snapshot?.tenantId ?? 'none', sourceId ?? 'none'],
    queryFn: () => getSource(snapshot as AuthSnapshot, sourceId as string),
    enabled: snapshot !== null && sourceId !== null,
    staleTime: Infinity,
    retry: false,
  })
}

// The CRUD mutations invalidate the shared sources prefix so the index (and detail)
// refetch and reflect the change.
export function useCreateSource(snapshot: AuthSnapshot | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (draft: SourceDraft) => createSource(snapshot as AuthSnapshot, draft),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SOURCES_KEY }),
  })
}

export function useUpdateSource(snapshot: AuthSnapshot | null, sourceId: string | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (patch: Pick<SourceDraft, 'name' | 'type' | 'store'>) =>
      updateSource(snapshot as AuthSnapshot, sourceId as string, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SOURCES_KEY }),
  })
}

export function useDeprecateSource(snapshot: AuthSnapshot | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sourceId: string) => deprecateSource(snapshot as AuthSnapshot, sourceId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SOURCES_KEY }),
  })
}
