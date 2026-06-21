import Papa from 'papaparse'

import type { ColumnProfile } from '../dis-ui-server/mapping-suggestions'

// Client-side CSV analysis (T11): parse the uploaded file in the browser with Papa Parse and
// build a real column profile (no backend parse, no demo). Per column: name, inferred datatype
// in the canonical vocabulary (integer | number | datetime | text | choice), null rate, and a
// few sample values. Also returns the first 10 sample rows (for the review preview) and the
// true data-row count. Bounded: inference runs over the first PROFILE_SAMPLE_CAP rows so a
// large file does not hang the UI, while row_count still reports the true total.

export type ParsedCsv = {
  columns: ColumnProfile[]
  sample_rows: Record<string, string>[] // first 10 data rows, keyed by header
  row_count: number // true number of data rows (excludes the header)
}

// Inference cap: profile/null-rate over at most this many rows (the 10 MB upload cap bounds
// the whole file; this bounds the per-column scan further so inference is never the hang).
const PROFILE_SAMPLE_CAP = 1000
const SAMPLE_ROWS = 10
const SAMPLE_VALUES = 3

const INTEGER_RE = /^-?\d+$/
const NUMBER_RE = /^-?\d+(\.\d+)?$/
// yyyy-mm-dd / dd-mm-yyyy / dd/mm/yyyy with an optional time; deliberately regex-based so plain
// integers/numbers are never mistaken for dates (Date.parse would accept bare years).
const DATE_RE = /^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2})?)?$/

const CHOICE_MAX_DISTINCT = 8

function isEmpty(value: string): boolean {
  return value.trim() === ''
}

// Infer a canonical datatype from a column's sampled values. Ordered so numeric/date win over
// choice (e.g. repeated small integers are integer, not choice); choice is only for
// low-cardinality non-numeric, non-date columns.
export function inferDatatype(values: string[]): ColumnProfile['inferred_datatype'] {
  const nonEmpty = values.map((v) => v.trim()).filter((v) => v !== '')
  if (nonEmpty.length === 0) {
    return 'text'
  }
  if (nonEmpty.every((v) => INTEGER_RE.test(v))) {
    return 'integer'
  }
  if (nonEmpty.every((v) => NUMBER_RE.test(v))) {
    return 'number'
  }
  if (nonEmpty.every((v) => DATE_RE.test(v))) {
    return 'datetime'
  }
  const distinct = new Set(nonEmpty).size
  if (nonEmpty.length >= 2 && distinct <= CHOICE_MAX_DISTINCT && distinct < nonEmpty.length * 0.5) {
    return 'choice'
  }
  return 'text'
}

function nullPct(values: string[]): number {
  if (values.length === 0) {
    return 0
  }
  const empties = values.filter(isEmpty).length
  return empties / values.length
}

function sampleValues(values: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const v of values) {
    const trimmed = v.trim()
    if (trimmed !== '' && !seen.has(trimmed)) {
      seen.add(trimmed)
      out.push(trimmed)
      if (out.length >= SAMPLE_VALUES) {
        break
      }
    }
  }
  return out
}

// Parse CSV text into a column profile. Quoted fields, delimiters, and embedded newlines are
// handled by Papa's defaults; the header row names the columns.
export function parseCsvText(text: string): ParsedCsv {
  const result = Papa.parse<Record<string, string>>(text, {
    header: true,
    skipEmptyLines: true,
  })
  const headers = result.meta.fields ?? []
  const rows = result.data
  const profilingRows = rows.slice(0, PROFILE_SAMPLE_CAP)

  const columns: ColumnProfile[] = headers.map((name) => {
    const values = profilingRows.map((row) => row[name] ?? '')
    return {
      name,
      inferred_datatype: inferDatatype(values),
      null_pct: nullPct(values),
      sample_values: sampleValues(values),
    }
  })

  return {
    columns,
    sample_rows: rows.slice(0, SAMPLE_ROWS),
    row_count: rows.length,
  }
}

// Read the File's bytes and analyze them (fully client-side). file.text() is bounded by the
// dropzone's 10 MB cap.
export async function parseCsvFile(file: File): Promise<ParsedCsv> {
  const text = await file.text()
  return parseCsvText(text)
}
