import type { AtlasField, AtlasTable, DraftPatch, FieldEdit } from '../../lib/dis-ui-server/atlas'

// Pure state machine for the Atlas ratify grid, mirroring connector-setup/state.ts. It holds
// ONLY the operator's uncommitted edits (per-field attribute changes + the pending natural key);
// the server-returned AtlasDraft is NOT held here, it is passed into the helpers (exactly as the
// connector wizard passes the suggestion set into its gating helpers). The route owns the side
// effects (PATCH, then publish) and dispatches actions into this reducer.
//
// RATIFY-ON-EDIT (the A4 invariant "every edit flips the value to origin: human"): every edit
// action sets that field's pending origin to 'human', so the origin marker flips immediately and
// PURELY (no network), and the field drops out of "remaining to ratify".
//
// THE ONE DESIGN RULE lives in remainingToRatify/canPublish below: they read curated_bearing and
// origin OFF THE WIRE (the AtlasField) and never recompute the curated-bearing predicate. The
// publish button disables on canPublish as a CONVENIENCE; the server 422 is authoritative.

export type RatifyState = {
  // sourceField name -> the operator's pending edit (always carries origin: 'human' once touched).
  edits: Record<string, FieldEdit>
  // The pending natural key. null = untouched (use the draft's); a list = the operator set it.
  naturalKey: string[] | null
}

export const initialRatifyState: RatifyState = {
  edits: {},
  naturalKey: null,
}

export type RatifyAction =
  | { type: 'setFieldNullable'; name: string; value: boolean }
  | { type: 'setFieldMandatory'; name: string; value: boolean }
  | { type: 'setFieldPii'; name: string; value: string | null }
  | { type: 'setFieldEnumCandidate'; name: string; values: string[] }
  | { type: 'setNaturalKey'; key: string[] }
  // Clear all uncommitted edits. The route dispatches this after a successful PATCH, once the
  // edits are PERSISTED and the draft cache is reconciled, so helpers fall back to the server's
  // (now ratified) origins rather than stale pre-PATCH edits.
  | { type: 'reset' }

// Merge an attribute change into a field's pending edit, stamping origin: 'human' (ratify-on-edit).
function mergeEdit(state: RatifyState, name: string, partial: Partial<FieldEdit>): RatifyState {
  const prior = state.edits[name] ?? { name }
  return {
    ...state,
    edits: { ...state.edits, [name]: { ...prior, ...partial, name, origin: 'human' } },
  }
}

export function ratifyReducer(state: RatifyState, action: RatifyAction): RatifyState {
  switch (action.type) {
    case 'setFieldNullable':
      return mergeEdit(state, action.name, { nullable: action.value })
    case 'setFieldMandatory':
      return mergeEdit(state, action.name, { mandatory: action.value })
    case 'setFieldPii':
      return mergeEdit(state, action.name, { pii: action.value })
    case 'setFieldEnumCandidate':
      return mergeEdit(state, action.name, { enum_candidate: action.values })
    case 'setNaturalKey':
      return { ...state, naturalKey: action.key }
    case 'reset':
      return initialRatifyState
    default:
      return state
  }
}

// ---------------------------------------------------------------------------------------------
// Pure views over (server field + local edit). The local edit wins (the override), mirroring the
// connector wizard's mappingTargetFor pattern.

// The effective origin marker: the local edit's origin if the field was touched, else the
// server's origin. This is what the grid's "inferred vs ratified" badge reads.
export function originFor(state: RatifyState, field: AtlasField): AtlasField['origin'] {
  return state.edits[field.name]?.origin ?? field.origin
}

export function nullableFor(state: RatifyState, field: AtlasField): boolean {
  return state.edits[field.name]?.nullable ?? field.nullable
}

export function mandatoryFor(state: RatifyState, field: AtlasField): boolean {
  return state.edits[field.name]?.mandatory ?? field.mandatory
}

export function piiFor(state: RatifyState, field: AtlasField): string | null {
  const edit = state.edits[field.name]
  return edit !== undefined && edit.pii !== undefined ? edit.pii : field.pii
}

export function enumCandidateFor(state: RatifyState, field: AtlasField): string[] {
  return state.edits[field.name]?.enum_candidate ?? field.enum_candidate
}

// The effective natural key: the operator's pending key if set, else the draft's.
export function effectiveNaturalKey(state: RatifyState, table: AtlasTable): string[] {
  return state.naturalKey ?? table.natural_key
}

export function hasPendingEdits(state: RatifyState): boolean {
  return Object.keys(state.edits).length > 0 || state.naturalKey !== null
}

// THE ONE DESIGN RULE (1/2): the curated-bearing fields still NOT human-ratified. Reads
// field.curated_bearing straight off the wire (server-derived); never recomputes the predicate.
// A natural_key member is curated_bearing server-side, so a "set but inferred-member" natural key
// is caught here, mirroring the backend ratify_violations per-field check.
export function remainingToRatify(state: RatifyState, table: AtlasTable): AtlasField[] {
  return table.fields.filter((f) => f.curated_bearing && originFor(state, f) !== 'human')
}

// THE ONE DESIGN RULE (2/2): a merge_upsert table needs a non-empty (ratified) natural key.
// Mirrors ratify_violations' `table.semantics == "merge_upsert" and not table.natural_key`.
export function naturalKeyUnsatisfied(state: RatifyState, table: AtlasTable): boolean {
  return table.semantics === 'merge_upsert' && effectiveNaturalKey(state, table).length === 0
}

// The publish-button enablement: a CONVENIENCE mirror of the server gate. The server 422 stays
// authoritative; this only saves a round-trip when the client can already see it is not ratified.
export function canPublish(state: RatifyState, table: AtlasTable): boolean {
  return remainingToRatify(state, table).length === 0 && !naturalKeyUnsatisfied(state, table)
}

// Batch the uncommitted edits into a single DraftPatch for PATCH. natural_key is sent only when
// the operator set it (null = leave the draft's as is).
export function toDraftPatch(state: RatifyState): DraftPatch {
  const patch: DraftPatch = { fields: Object.values(state.edits) }
  if (state.naturalKey !== null) {
    patch.natural_key = state.naturalKey
  }
  return patch
}
