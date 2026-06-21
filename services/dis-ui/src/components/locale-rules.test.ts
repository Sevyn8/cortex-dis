import {
  DATE_FORMAT_CHOICES,
  DECIMAL_CHOICES,
  buildNormalizeSpec,
  isRuleComplete,
  requiredRuleKind,
} from './locale-rules'

// T3: the locale rules are mandatory-by-datatype, never inferred, and feed the REAL
// mapping_rules.normalize {op, args} shape.
describe('locale-rules', () => {
  it('maps datatype to the required rule kind', () => {
    expect(requiredRuleKind('date')).toBe('date')
    expect(requiredRuleKind('datetime')).toBe('datetime')
    expect(requiredRuleKind('number')).toBe('decimal')
    // text / integer / boolean / choice / json: no mandatory locale rule
    expect(requiredRuleKind('text')).toBeNull()
    expect(requiredRuleKind('integer')).toBeNull()
    expect(requiredRuleKind('boolean')).toBeNull()
  })

  it('every choice carries a visible example', () => {
    for (const c of DATE_FORMAT_CHOICES) {
      expect(c.example).toMatch(/->/)
    }
    for (const c of DECIMAL_CHOICES) {
      expect(c.example).toMatch(/->/)
    }
    // the classic EU vs US separator examples are present
    expect(DECIMAL_CHOICES.map((c) => c.example)).toEqual(
      expect.arrayContaining(['1,299.50 -> 1299.50', '1.299,50 -> 1299.50']),
    )
  })

  it('isRuleComplete is false until the required args are declared', () => {
    expect(isRuleComplete(null, undefined)).toBe(true) // no rule needed
    expect(isRuleComplete('decimal', undefined)).toBe(false)
    expect(isRuleComplete('decimal', { decimal_separator: ',' })).toBe(true)
    expect(isRuleComplete('date', {})).toBe(false)
    expect(isRuleComplete('date', { format: '%d-%m-%Y' })).toBe(true)
    // datetime needs BOTH format and timezone (the real parse_datetime contract)
    expect(isRuleComplete('datetime', { format: '%d-%m-%Y' })).toBe(false)
    expect(isRuleComplete('datetime', { format: '%d-%m-%Y', timezone: 'UTC' })).toBe(true)
  })

  it('buildNormalizeSpec emits the real {op, args} shape, or null when incomplete', () => {
    expect(buildNormalizeSpec('decimal', undefined)).toBeNull()
    expect(buildNormalizeSpec('decimal', { decimal_separator: ',', thousands_separator: '.' })).toEqual({
      op: 'parse_decimal',
      args: { decimal_separator: ',', thousands_separator: '.' },
    })
    // thousands optional -> null when not declared
    expect(buildNormalizeSpec('decimal', { decimal_separator: '.' })).toEqual({
      op: 'parse_decimal',
      args: { decimal_separator: '.', thousands_separator: null },
    })
    expect(buildNormalizeSpec('date', { format: '%d-%m-%Y' })).toEqual({
      op: 'parse_date',
      args: { format: '%d-%m-%Y' },
    })
    expect(buildNormalizeSpec('datetime', { format: '%d-%m-%Y', timezone: 'UTC' })).toEqual({
      op: 'parse_datetime',
      args: { format: '%d-%m-%Y', timezone: 'UTC' },
    })
  })
})
