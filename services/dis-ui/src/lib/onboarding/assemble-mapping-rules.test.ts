import { describe, expect, it } from 'vitest'

import { assembleMappingRules } from './assemble-mapping-rules'
import type { AssemblyColumn } from './assemble-mapping-rules'

function col(partial: Partial<AssemblyColumn> & { source_col: string; proposed_canonical: string }): AssemblyColumn {
  return { rule_kind: null, locale: undefined, ...partial }
}

describe('assembleMappingRules', () => {
  it('builds rename from mapped columns and skips do-not-map ("")', () => {
    const res = assembleMappingRules([
      col({ source_col: 'item_code', proposed_canonical: 'sku_id' }),
      col({ source_col: 'noise', proposed_canonical: '' }), // do not map
    ])
    expect(res.ok).toBe(true)
    if (!res.ok) return
    expect(res.rules.rename).toEqual({ item_code: 'sku_id' })
    expect(res.rules.rename).not.toHaveProperty('noise')
  })

  it('returns an error when two columns map to the same canonical (rename-uniqueness)', () => {
    const res = assembleMappingRules([
      col({ source_col: 'a', proposed_canonical: 'sku_id' }),
      col({ source_col: 'b', proposed_canonical: 'sku_id' }),
    ])
    expect(res.ok).toBe(false)
    if (res.ok) return
    expect(res.error).toMatch(/two columns map to "sku_id"/i)
  })

  it('keys normalize by the canonical target and builds the spec via buildNormalizeSpec', () => {
    const res = assembleMappingRules([
      col({
        source_col: 'qty',
        proposed_canonical: 'quantity',
        rule_kind: 'decimal',
        locale: { decimal_separator: '.' }, // thousands undeclared
      }),
    ])
    expect(res.ok).toBe(true)
    if (!res.ok) return
    expect(res.rules.normalize.quantity).toEqual([
      { op: 'parse_decimal', args: { decimal_separator: '.', thousands_separator: null } },
    ])
  })

  it('parse_decimal sends thousands_separator: null when undeclared (key present, not omitted)', () => {
    const res = assembleMappingRules([
      col({ source_col: 'qty', proposed_canonical: 'quantity', rule_kind: 'decimal', locale: { decimal_separator: ',' } }),
    ])
    expect(res.ok).toBe(true)
    if (!res.ok) return
    const args = res.rules.normalize.quantity[0].args
    expect('thousands_separator' in args).toBe(true) // key MUST be present (server requires it)
    expect(args.thousands_separator).toBeNull()
  })

  it('builds a parse_datetime spec with format + timezone, keyed by canonical', () => {
    const res = assembleMappingRules([
      col({
        source_col: 'txn_date',
        proposed_canonical: 'source_sale_timestamp',
        rule_kind: 'datetime',
        locale: { format: '%d-%m-%Y', timezone: 'UTC' },
      }),
    ])
    expect(res.ok).toBe(true)
    if (!res.ok) return
    expect(res.rules.normalize.source_sale_timestamp).toEqual([
      { op: 'parse_datetime', args: { format: '%d-%m-%Y', timezone: 'UTC' } },
    ])
  })

  it('produces EXACTLY the five keys (extra=forbid), with cast and derive empty', () => {
    const res = assembleMappingRules([col({ source_col: 'item_code', proposed_canonical: 'sku_id' })])
    expect(res.ok).toBe(true)
    if (!res.ok) return
    expect(Object.keys(res.rules).sort()).toEqual(['cast', 'derive', 'normalize', 'rename', 'version'])
    expect(res.rules.version).toBe(1)
    expect(res.rules.cast).toEqual({})
    expect(res.rules.derive).toEqual({})
  })
})
