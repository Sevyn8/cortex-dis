import type { FieldDatatype } from '../lib/dis-ui-server/mapping-fields'

// Locale / format rules (T3). A mapping is two concerns: the FIELD mapping (column ->
// canonical field) and the FORMAT rules (how a value is normalized: date format, decimal
// separator). The locale rules are MANDATORY by the mapped field's datatype and NEVER
// inferred (D49 "asserted, never defaulted"; libs/dis-mapping validates required args and
// raises if missing). This module is the single source of: which rule a datatype requires,
// the human choices (with visual examples), and how a declaration builds the REAL
// mapping_rules.normalize shape the shipped normalizer consumes.
//
// The real shape (libs/dis-mapping/models/transform.py + engine/normalize.py):
//   TransformSpec = { op, args }  (args NESTED, not flat)
//   parse_date     args = { format }                          # format is a polars strptime string
//   parse_datetime args = { format, timezone }                # both required
//   parse_decimal  args = { decimal_separator, thousands_separator }   # thousands optional
export type TransformSpec = { op: string; args: Record<string, unknown> }

// Which mandatory locale rule a mapped field's datatype implies (null = no locale rule).
export type RuleKind = 'date' | 'datetime' | 'decimal' | null

export function requiredRuleKind(datatype: FieldDatatype): RuleKind {
  if (datatype === 'date') {
    return 'date'
  }
  if (datatype === 'datetime') {
    return 'datetime'
  }
  if (datatype === 'number') {
    return 'decimal'
  }
  // integer / text / boolean / choice / json: no mandatory locale rule
  return null
}

// Human date-format choices -> the polars strptime `format` value, each with a visual
// example so the operator declares the format by recognition, not by guessing codes.
export type DateFormatChoice = { value: string; label: string; example: string }
export const DATE_FORMAT_CHOICES: DateFormatChoice[] = [
  { value: '%d-%m-%Y', label: 'Day-Month-Year (dashes)', example: '31-12-2025 -> 2025-12-31' },
  { value: '%d/%m/%Y', label: 'Day/Month/Year (slashes)', example: '31/12/2025 -> 2025-12-31' },
  { value: '%m/%d/%Y', label: 'Month/Day/Year (slashes)', example: '12/31/2025 -> 2025-12-31' },
  { value: '%Y-%m-%d', label: 'Year-Month-Day (ISO)', example: '2025-12-31 -> 2025-12-31' },
  { value: '%d-%m-%y', label: 'Day-Month-Year, 2-digit year', example: '31-12-25 -> 2025-12-31' },
]

// Decimal-separator choices, each with the "input -> parsed" example that makes the
// difference unmistakable (the classic EU vs US ambiguity).
export type DecimalChoice = { value: string; label: string; example: string }
export const DECIMAL_CHOICES: DecimalChoice[] = [
  { value: '.', label: 'Point (1,299.50)', example: '1,299.50 -> 1299.50' },
  { value: ',', label: 'Comma (1.299,50)', example: '1.299,50 -> 1299.50' },
]
// Optional thousands separator. '' = none declared.
export type ThousandsChoice = { value: string; label: string }
export const THOUSANDS_CHOICES: ThousandsChoice[] = [
  { value: '', label: 'None' },
  { value: ',', label: 'Comma' },
  { value: '.', label: 'Point' },
  { value: ' ', label: 'Space' },
]

// A timezone is required for a datetime field (parse_datetime). Common options; the
// onboarded stores supply the tenant-relevant zones, merged in by the screen.
export const COMMON_TIMEZONES: string[] = ['UTC', 'America/New_York', 'America/Chicago', 'Europe/London', 'Asia/Kolkata']

// One column's locale declaration (screen-local). Empty strings = undeclared (never
// defaulted). `format`/`timezone` for date/datetime; `decimal_separator`/`thousands`
// for decimal.
export type LocaleDeclaration = {
  format?: string
  timezone?: string
  decimal_separator?: string
  thousands_separator?: string
}

// Is the required declaration complete (all mandatory args declared)? Drives the gate.
export function isRuleComplete(kind: RuleKind, decl: LocaleDeclaration | undefined): boolean {
  if (kind === null) {
    return true // no rule required
  }
  if (decl === undefined) {
    return false
  }
  if (kind === 'date') {
    return Boolean(decl.format)
  }
  if (kind === 'datetime') {
    return Boolean(decl.format) && Boolean(decl.timezone)
  }
  // decimal: decimal_separator mandatory; thousands optional
  return Boolean(decl.decimal_separator)
}

// Build the REAL mapping_rules.normalize TransformSpec from a declaration, or null when
// incomplete (so the screen never emits a half-declared, never-inferred rule).
export function buildNormalizeSpec(kind: RuleKind, decl: LocaleDeclaration | undefined): TransformSpec | null {
  if (kind === null || !isRuleComplete(kind, decl) || decl === undefined) {
    return null
  }
  if (kind === 'date') {
    return { op: 'parse_date', args: { format: decl.format } }
  }
  if (kind === 'datetime') {
    return { op: 'parse_datetime', args: { format: decl.format, timezone: decl.timezone } }
  }
  // decimal -> parse_decimal; thousands_separator null when not declared
  return {
    op: 'parse_decimal',
    args: {
      decimal_separator: decl.decimal_separator,
      thousands_separator: decl.thousands_separator ? decl.thousands_separator : null,
    },
  }
}
