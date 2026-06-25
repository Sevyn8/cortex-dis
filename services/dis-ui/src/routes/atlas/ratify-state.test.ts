import { describe, expect, it } from 'vitest'

import type { AtlasField, AtlasTable } from '../../lib/dis-ui-server/atlas'
import {
  canPublish,
  initialRatifyState,
  naturalKeyUnsatisfied,
  originFor,
  ratifyReducer,
  remainingToRatify,
  toDraftPatch,
} from './ratify-state'

function field(name: string, extra: Partial<AtlasField> = {}): AtlasField {
  return {
    name,
    produced_by: 'mapping_produced',
    type_ref: 'text',
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
  }
}

function table(fields: AtlasField[], extra: Partial<AtlasTable> = {}): AtlasTable {
  return {
    key: 'store_sku_current_position',
    template_type: 'snapshot',
    semantics: 'merge_upsert',
    sink: 'canonical.store_sku_current_position',
    natural_key: [],
    fields,
    ...extra,
  }
}

describe('ratifyReducer', () => {
  it('an edit stamps the field origin human (ratify-on-edit) and records the attribute', () => {
    const f = field('on_hand_qty', { origin: 'inferred' })
    const next = ratifyReducer(initialRatifyState, {
      type: 'setFieldMandatory',
      name: 'on_hand_qty',
      value: true,
    })
    expect(originFor(next, f)).toBe('human')
    expect(next.edits['on_hand_qty']).toEqual({
      name: 'on_hand_qty',
      mandatory: true,
      origin: 'human',
    })
  })

  it('merges multiple edits to one field, keeping origin human', () => {
    let s = ratifyReducer(initialRatifyState, { type: 'setFieldNullable', name: 'x', value: true })
    s = ratifyReducer(s, { type: 'setFieldPii', name: 'x', value: 'pii' })
    expect(s.edits['x']).toEqual({ name: 'x', nullable: true, pii: 'pii', origin: 'human' })
  })

  it('setNaturalKey records the pending key; reset clears all edits', () => {
    let s = ratifyReducer(initialRatifyState, {
      type: 'setNaturalKey',
      key: ['store_id', 'sku_id'],
    })
    s = ratifyReducer(s, { type: 'setFieldMandatory', name: 'store_id', value: true })
    expect(s.naturalKey).toEqual(['store_id', 'sku_id'])
    const cleared = ratifyReducer(s, { type: 'reset' })
    expect(cleared).toEqual(initialRatifyState)
  })

  it('toDraftPatch batches edits and includes natural_key only when set', () => {
    let s = ratifyReducer(initialRatifyState, { type: 'setFieldMandatory', name: 'a', value: true })
    expect(toDraftPatch(s)).toEqual({ fields: [{ name: 'a', mandatory: true, origin: 'human' }] })
    s = ratifyReducer(s, { type: 'setNaturalKey', key: ['a'] })
    expect(toDraftPatch(s)).toEqual({
      fields: [{ name: 'a', mandatory: true, origin: 'human' }],
      natural_key: ['a'],
    })
  })
})

describe('canPublish / remainingToRatify (THE ONE DESIGN RULE, client convenience)', () => {
  it('is DISABLED while a curated-bearing field is still origin inferred', () => {
    const t = table(
      [
        field('store_id', { curated_bearing: true, origin: 'inferred', mandatory: true }),
        field('sku_id', { curated_bearing: true, origin: 'human', mandatory: true }),
      ],
      { natural_key: ['store_id', 'sku_id'] },
    )
    expect(remainingToRatify(initialRatifyState, t).map((f) => f.name)).toEqual(['store_id'])
    expect(canPublish(initialRatifyState, t)).toBe(false)
  })

  it('is ENABLED once every curated-bearing field is human and the natural key is set', () => {
    const t = table(
      [
        field('store_id', { curated_bearing: true, origin: 'human', mandatory: true }),
        field('sku_id', { curated_bearing: true, origin: 'human', mandatory: true }),
        field('on_hand_qty', { curated_bearing: false, origin: 'inferred' }),
      ],
      { natural_key: ['store_id', 'sku_id'] },
    )
    expect(remainingToRatify(initialRatifyState, t)).toEqual([])
    expect(naturalKeyUnsatisfied(initialRatifyState, t)).toBe(false)
    expect(canPublish(initialRatifyState, t)).toBe(true)
  })

  it('a local ratify-on-edit flips a remaining field and enables publish (no network)', () => {
    const t = table(
      [field('store_id', { curated_bearing: true, origin: 'inferred', mandatory: true })],
      { natural_key: ['store_id'] },
    )
    expect(canPublish(initialRatifyState, t)).toBe(false)
    const ratified = ratifyReducer(initialRatifyState, {
      type: 'setFieldMandatory',
      name: 'store_id',
      value: true,
    })
    expect(canPublish(ratified, t)).toBe(true)
  })

  it('NATURAL-KEY MEMBERS: a set natural_key with an inferred member stays DISABLED', () => {
    // Mirrors the backend ratify_violations load-bearing case: natural_key members are
    // curated_bearing server-side, so a key that "looks set" but whose member is still inferred
    // is caught by the per-field check, NOT only the empty-key check. The client agrees.
    const t = table(
      [
        // natural_key is non-empty (so naturalKeyUnsatisfied is false) but store_id is still inferred.
        field('store_id', { curated_bearing: true, origin: 'inferred', mandatory: true }),
        field('sku_id', { curated_bearing: true, origin: 'human', mandatory: true }),
      ],
      { natural_key: ['store_id', 'sku_id'] },
    )
    expect(naturalKeyUnsatisfied(initialRatifyState, t)).toBe(false) // the key is set
    expect(remainingToRatify(initialRatifyState, t).map((f) => f.name)).toEqual(['store_id']) // member caught
    expect(canPublish(initialRatifyState, t)).toBe(false)
  })

  it('an empty natural_key on a merge_upsert table disables publish even with no curated fields', () => {
    const t = table([field('a', { curated_bearing: false })], { natural_key: [] })
    expect(remainingToRatify(initialRatifyState, t)).toEqual([])
    expect(naturalKeyUnsatisfied(initialRatifyState, t)).toBe(true)
    expect(canPublish(initialRatifyState, t)).toBe(false)
  })

  it('a non-merge_upsert table does not require a natural key', () => {
    const t = table([field('a', { curated_bearing: false })], {
      semantics: 'append_only',
      natural_key: [],
    })
    expect(naturalKeyUnsatisfied(initialRatifyState, t)).toBe(false)
    expect(canPublish(initialRatifyState, t)).toBe(true)
  })
})
