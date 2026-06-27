import { useQuery } from '@tanstack/react-query'

import { getAccessToken } from './accessToken'
import { getJson, patchJson, postJson, postMultipart } from './client'
import { isRealMode } from './mode'

// Atlas console client (A4 PR3b). Mirrors the existing client seam: csv-uploads.ts (the
// multipart create) and template-types.ts (the isRealMode() real/fixture dual path + a
// react-query hook). The TS types match the dis-ui-server wire models FIELD-FOR-FIELD
// (services/dis-ui-server/src/dis_ui_server/schemas/atlas.py) so the grid reads the
// server's curated_bearing/origin straight off the wire and never re-derives them.
//
// The BFF is FROZEN (PR1/PR2/PR3a). This module only consumes it: GET /atlas/drafts (PR3a),
// POST /atlas/verticals/{vertical}/draft, GET/PATCH /atlas/drafts/{id}, POST .../publish.
// Routes live under the /api/v1 mount (api.py), so the paths below carry that prefix.

// ---------------------------------------------------------------------------------------------
// Wire types (field-for-field with schemas/atlas.py).

// AtlasFieldModel.provenance (the _field_to_wire provenance bag; null when the field has none).
export type AtlasFieldProvenance = {
  introduced_in: string
  source_headers: string[]
  present_in_files: number
  total_files: number
  rows_profiled: number
}

// AtlasFieldModel. `origin` is the human-override marker the ratify grid reads: 'inferred'
// (proposed, needs ratification) | 'human' (ratified) | null (system fields carry their own).
// `curated_bearing` is DERIVED SERVER-SIDE (is_curated_bearing) and arrives on the wire; the
// client reads it, it never recomputes the predicate (THE ONE DESIGN RULE).
export type AtlasField = {
  name: string
  produced_by: string
  type_ref: string
  nullable: boolean
  default: string | null
  mandatory: boolean
  max_length: number | null
  precision: number | null
  scale: number | null
  enum_ref: string | null
  enum_candidate: string[]
  pii: string | null
  origin: 'inferred' | 'human' | null
  display_name: string | null
  description: string | null
  provenance: AtlasFieldProvenance | null
  curated_bearing: boolean
}

// AtlasTableModel. `semantics` carries the merge_upsert signal the natural-key requirement
// keys on (ratify_violations: a merge_upsert table needs a non-empty ratified natural_key).
export type AtlasTable = {
  key: string
  template_type: string
  semantics: string
  sink: string
  natural_key: string[]
  fields: AtlasField[]
}

// AtlasDraftResponse: a draft (or frozen) IR over the wire.
export type AtlasDraft = {
  draft_id: string
  vertical: string
  status: string
  schema_version: number
  system_profile: string
  table: AtlasTable
}

// DraftSummaryModel: one lean registry row (no IR document). Timestamps are nullable (only the
// durable store has them; the in-memory double returns null).
export type DraftSummary = {
  draft_id: string
  vertical: string
  table_key: string
  status: string
  schema_version: number
  created_at: string | null
  updated_at: string | null
  published_at: string | null
}

// FieldEdit: one field edit. Only provided attributes change; `origin` flips on ratify.
export type FieldEdit = {
  name: string
  nullable?: boolean
  mandatory?: boolean
  pii?: string | null
  enum_candidate?: string[]
  origin?: 'inferred' | 'human'
}

// DraftPatch: the table-level natural key plus per-field edits.
export type DraftPatch = {
  natural_key?: string[]
  fields: FieldEdit[]
}

// PublishReceipt: the post-publish confirmation.
export type PublishReceipt = {
  draft_id: string
  vertical: string
  status: string
  schema_version: number
  audit_emitted: boolean
}

export type DraftStatusFilter = 'draft' | 'published' | 'superseded'

const ATLAS_BASE = '/api/v1/atlas'

// ---------------------------------------------------------------------------------------------
// Real callers (real mode) + fixture stand-ins (fixture mode, for local dev + the manual
// walkthrough). Tests mock this module, so the fixture realism matters only for run-local.

// GET /atlas/drafts(?status=). The lean registry list.
export async function listDrafts(status?: DraftStatusFilter): Promise<DraftSummary[]> {
  if (isRealMode()) {
    const query = status !== undefined ? `?status=${status}` : ''
    return getJson<DraftSummary[]>(`${ATLAS_BASE}/drafts${query}`)
  }
  const rows = fixtureSummaries()
  return status !== undefined ? rows.filter((r) => r.status === status) : rows
}

// POST /atlas/verticals/{vertical}/draft. Multipart: one or more example CSVs (files) plus an
// optional table_key (the csv-uploads multipart pattern; the browser sets the boundary).
export async function createDraft(
  vertical: string,
  files: File[],
  tableKey?: string,
): Promise<AtlasDraft> {
  if (isRealMode()) {
    const form = new FormData()
    for (const file of files) {
      form.append('files', file)
    }
    if (tableKey !== undefined && tableKey.length > 0) {
      form.append('table_key', tableKey)
    }
    return postMultipart<AtlasDraft>(`${ATLAS_BASE}/verticals/${vertical}/draft`, {
      token: await getAccessToken(),
      form,
    })
  }
  return fixtureCreateDraft(vertical, tableKey)
}

// GET /atlas/drafts/{id}. The full IR for the ratify grid / published detail.
export async function getDraft(draftId: string): Promise<AtlasDraft> {
  if (isRealMode()) {
    return getJson<AtlasDraft>(`${ATLAS_BASE}/drafts/${draftId}`)
  }
  return fixtureGetDraft(draftId)
}

// PATCH /atlas/drafts/{id}. Returns the UPDATED draft (origins flipped to human for ratified
// fields, natural_key set) so the caller reconciles local state with the persisted draft.
export async function patchDraft(draftId: string, patch: DraftPatch): Promise<AtlasDraft> {
  if (isRealMode()) {
    return patchJson<AtlasDraft>(`${ATLAS_BASE}/drafts/${draftId}`, patch)
  }
  return fixtureApplyPatch(fixtureGetDraft(draftId), patch)
}

// POST /atlas/drafts/{id}/publish. Runs the server ratify gate and freezes; a still-unratified
// draft 422s (draft_not_ratified) with details.violations (the AUTHORITATIVE gate).
export async function publishDraft(draftId: string): Promise<PublishReceipt> {
  if (isRealMode()) {
    return postJson<PublishReceipt>(`${ATLAS_BASE}/drafts/${draftId}/publish`, {})
  }
  return fixturePublish(fixtureGetDraft(draftId))
}

// ---------------------------------------------------------------------------------------------
// react-query hooks (the template-types.ts wrapper shape). retry off; the registry is volatile
// (a publish changes it) so it is not held Infinity-stale.

export function useDrafts(status?: DraftStatusFilter) {
  return useQuery({
    queryKey: ['dis-ui-server', 'atlas', 'drafts', status ?? 'all'],
    queryFn: () => listDrafts(status),
    retry: false,
  })
}

export function useDraft(draftId: string | undefined) {
  return useQuery({
    queryKey: ['dis-ui-server', 'atlas', 'draft', draftId],
    queryFn: () => getDraft(draftId as string),
    enabled: draftId !== undefined,
    retry: false,
  })
}

// The query key for a single draft, so callers can reconcile the cache after a PATCH (set the
// returned, persisted draft) without restating the tuple.
export function draftQueryKey(draftId: string): unknown[] {
  return ['dis-ui-server', 'atlas', 'draft', draftId]
}

// ---------------------------------------------------------------------------------------------
// Fixtures (fixture mode). Shaped to the contract: a retail snapshot draft with a merge_upsert
// table, an empty natural_key, and curated-bearing fields still origin: inferred, so the ratify
// grid and the publish gate are exercisable end to end in local dev.

function retailDraftFixture(draftId: string): AtlasDraft {
  const field = (name: string, typeRef: string, extra: Partial<AtlasField> = {}): AtlasField => ({
    name,
    produced_by: 'mapping_produced',
    type_ref: typeRef,
    nullable: false,
    default: null,
    mandatory: false,
    max_length: null,
    precision: null,
    scale: null,
    enum_ref: null,
    enum_candidate: [],
    pii: null,
    origin: 'inferred',
    display_name: null,
    description: null,
    provenance: null,
    curated_bearing: false,
    ...extra,
  })
  return {
    draft_id: draftId,
    vertical: 'retail',
    status: 'draft',
    schema_version: 1,
    system_profile: 'dis.v1',
    table: {
      key: 'store_sku_current_position',
      template_type: 'snapshot',
      semantics: 'merge_upsert',
      sink: 'canonical.store_sku_current_position',
      natural_key: [],
      fields: [
        field('store_id', 'text', { mandatory: true, curated_bearing: true }),
        field('sku_id', 'text', { mandatory: true, curated_bearing: true }),
        field('on_hand_qty', 'integer'),
        field('unit_cost', 'numeric', { precision: 12, scale: 2 }),
      ],
    },
  }
}

function fixtureSummaries(): DraftSummary[] {
  return [
    {
      draft_id: 'draft-fixture-0001',
      vertical: 'retail',
      table_key: 'store_sku_current_position',
      status: 'draft',
      schema_version: 1,
      created_at: null,
      updated_at: null,
      published_at: null,
    },
  ]
}

function fixtureCreateDraft(vertical: string, tableKey?: string): AtlasDraft {
  const draft = retailDraftFixture('draft-fixture-new')
  draft.vertical = vertical
  if (tableKey !== undefined && tableKey.length > 0) {
    draft.table.key = tableKey
  }
  return draft
}

function fixtureGetDraft(draftId: string): AtlasDraft {
  return retailDraftFixture(draftId)
}

// Fixture stand-in for the SERVER's _apply_patch: it sets provided attributes, flips origin to
// the provided value, sets the natural_key, and recomputes curated_bearing the way the server
// would (is_curated_bearing). This is the server simulation, NOT the grid predicate; the grid
// still reads curated_bearing off the wire and never recomputes it.
function fixtureApplyPatch(draft: AtlasDraft, patch: DraftPatch): AtlasDraft {
  const editByName = new Map(patch.fields.map((e) => [e.name, e]))
  const naturalKey = patch.natural_key ?? draft.table.natural_key
  const fields = draft.table.fields.map((f) => {
    const edit = editByName.get(f.name)
    const next: AtlasField = { ...f }
    if (edit !== undefined) {
      if (edit.nullable !== undefined) next.nullable = edit.nullable
      if (edit.mandatory !== undefined) next.mandatory = edit.mandatory
      if (edit.pii !== undefined) next.pii = edit.pii
      if (edit.enum_candidate !== undefined) next.enum_candidate = edit.enum_candidate
      if (edit.origin !== undefined) next.origin = edit.origin
    }
    next.curated_bearing =
      naturalKey.includes(next.name) ||
      next.mandatory ||
      next.enum_candidate.length > 0 ||
      (next.pii !== null && next.pii !== 'none')
    return next
  })
  return { ...draft, table: { ...draft.table, natural_key: naturalKey, fields } }
}

function fixturePublish(draft: AtlasDraft): PublishReceipt {
  return {
    draft_id: draft.draft_id,
    vertical: draft.vertical,
    status: 'published',
    schema_version: draft.schema_version,
    audit_emitted: false,
  }
}
