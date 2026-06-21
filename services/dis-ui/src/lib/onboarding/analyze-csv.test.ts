import { describe, expect, it } from 'vitest'

import { inferDatatype, parseCsvText } from './analyze-csv'

describe('inferDatatype (canonical vocabulary)', () => {
  it('all integers -> integer', () => {
    expect(inferDatatype(['1', '2', '-3'])).toBe('integer')
  })
  it('decimals -> number', () => {
    expect(inferDatatype(['1.5', '2', '3.25'])).toBe('number')
  })
  it('parseable dates -> datetime', () => {
    expect(inferDatatype(['03-12-2025', '2025-12-04', '05/12/2025'])).toBe('datetime')
  })
  it('low-cardinality non-numeric -> choice', () => {
    expect(inferDatatype(['USD', 'EUR', 'USD', 'EUR', 'USD', 'EUR'])).toBe('choice')
  })
  it('free text -> text', () => {
    expect(inferDatatype(['hello world', 'a longer description', 'yet another value'])).toBe('text')
  })
  it('all empty -> text', () => {
    expect(inferDatatype(['', '  ', ''])).toBe('text')
  })
})

describe('parseCsvText', () => {
  it('builds a column profile (types, null_pct, sample_values) + true row_count', () => {
    const csv = 'item_code,qty,txn_date\nA123,12,03-12-2025\nB456,,04-12-2025\n'
    const parsed = parseCsvText(csv)
    expect(parsed.row_count).toBe(2)
    const byCol = Object.fromEntries(parsed.columns.map((c) => [c.name, c]))
    expect(byCol.item_code.inferred_datatype).toBe('text')
    expect(byCol.qty.inferred_datatype).toBe('integer')
    expect(byCol.qty.null_pct).toBe(0.5) // one empty of two rows
    expect(byCol.txn_date.inferred_datatype).toBe('datetime')
    expect(byCol.item_code.sample_values).toContain('A123')
  })

  it('caps the sample preview at 10 rows but reports the true row_count', () => {
    const body = Array.from({ length: 15 }, (_, i) => `A${i},${i}`).join('\n')
    const parsed = parseCsvText(`sku,qty\n${body}\n`)
    expect(parsed.row_count).toBe(15)
    expect(parsed.sample_rows).toHaveLength(10)
  })

  it('handles quoted fields with embedded commas (Papa defaults)', () => {
    const parsed = parseCsvText('name,note\n"Acme, Inc.","a, b, c"\n')
    expect(parsed.row_count).toBe(1)
    expect(parsed.sample_rows[0].name).toBe('Acme, Inc.')
    expect(parsed.sample_rows[0].note).toBe('a, b, c')
  })
})
