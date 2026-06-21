import { postJson } from './client'
import type { FieldDatatype, TemplateMappingField } from './mapping-fields'
import { isRealMode } from './mode'

// Mapping-suggestion endpoint client (T11). Types mirror the BFF schemas EXACTLY
// (services/dis-ui-server/src/dis_ui_server/schemas/mapping_suggestions.py): the frontend
// sends the parsed column PROFILE; the server returns per-column suggestions plus a `source`
// flag ("llm" when Gemini produced them, "fallback" when the mechanical matcher did) so the
// UI labels honestly. In REAL mode this POSTs to /api/v1/mapping-suggestions; in FIXTURE mode
// it computes a local stand-in with the SAME mechanical-matcher logic the server uses as its
// fallback, so local dev and a keyless deployment behave identically.

export type SuggestionSource = 'llm' | 'fallback'

// MappingSuggestionRequest.ColumnProfile (server: name, inferred_datatype, null_pct, sample_values).
export type ColumnProfile = {
  name: string
  inferred_datatype: string // canonical vocab: integer | number | datetime | text | choice
  null_pct: number // 0..1
  sample_values: string[]
}

export type MappingSuggestionRequest = {
  columns: ColumnProfile[]
  source_id?: string | null
  template_name?: string | null
  // Type-aware suggestions (D90): the server scores against THIS type's per-type catalog
  // (snapshot included). REQUIRED now that the legacy /upload flow (the only caller that omitted
  // it) is retired: every caller is type-aware (connectors-api.analyzeCsvSample). The fixture
  // mechanicalSuggest ignores it. NOTE: the dis-ui-server handler still keeps a no-template_type
  // union fallback (unreachable dead code post-retirement) - removing it is a deferred backend
  // follow-up (dis-ui-server domain), not bundled into this frontend cleanup.
  template_type: string
}

// MappingSuggestionResponse.Suggestion. `suggested_target` is a catalog key or null;
// `alternatives` are catalog keys (server list[str]); `reasoning` is LLM-path only.
export type Suggestion = {
  source_column: string
  suggested_target: string | null
  confidence: number
  reasoning?: string | null
  alternatives?: string[] | null
}

export type MappingSuggestionResponse = {
  source: SuggestionSource
  model?: string | null
  suggestions: Suggestion[]
}

// ---------------------------------------------------------------------------------------------
// Mechanical fallback (fixture mode). Ports the server fallback_matcher.py logic verbatim:
// normalize the column name, score against catalog keys/display names + a small retail synonym
// map, with a bonus when the inferred datatype is compatible with the candidate's datatype;
// argmax key, score as confidence. No reasoning/alternatives (a mechanical match has none).

// Normalized column-name synonyms -> catalog key (mirrors the server map).
const SYNONYMS: Record<string, string> = {
  itemcode: 'sku_id',
  item: 'sku_id',
  sku: 'sku_id',
  product: 'sku_id',
  productcode: 'sku_id',
  articlenumber: 'sku_id',
  variant: 'sku_variant',
  skuvariant: 'sku_variant',
  qty: 'quantity',
  quantity: 'quantity',
  units: 'quantity',
  unitssold: 'quantity',
  price: 'unit_sale_price',
  unitprice: 'unit_sale_price',
  saleprice: 'unit_sale_price',
  sellprice: 'unit_sale_price',
  amount: 'unit_sale_price',
  retailprice: 'unit_retail_price',
  listprice: 'unit_retail_price',
  txndate: 'source_sale_timestamp',
  date: 'source_sale_timestamp',
  saledate: 'source_sale_timestamp',
  soldat: 'source_sale_timestamp',
  timestamp: 'source_sale_timestamp',
  datetime: 'source_sale_timestamp',
  time: 'source_sale_timestamp',
  txn: 'transaction_id',
  transaction: 'transaction_id',
  transactionid: 'transaction_id',
  terminal: 'transaction_id',
  register: 'transaction_id',
  pos: 'transaction_id',
  posterminal: 'transaction_id',
  currency: 'currency',
  ccy: 'currency',
  description: 'product_description',
  productdescription: 'product_description',
}

// Inferred datatype (UI vocab) -> compatible catalog datatypes (a small tie-breaking bonus).
const DATATYPE_COMPAT: Record<string, ReadonlySet<FieldDatatype>> = {
  integer: new Set<FieldDatatype>(['integer', 'number']),
  number: new Set<FieldDatatype>(['number', 'integer']),
  datetime: new Set<FieldDatatype>(['datetime', 'date']),
  date: new Set<FieldDatatype>(['date', 'datetime']),
  text: new Set<FieldDatatype>(['text', 'choice']),
  choice: new Set<FieldDatatype>(['choice', 'text']),
}

function normalize(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '')
}

function nameScore(normalizedCol: string, field: TemplateMappingField): number {
  const keyNorm = normalize(field.key)
  const displayNorm = normalize(field.display_name)
  if (SYNONYMS[normalizedCol] === field.key) {
    return 0.95
  }
  if (normalizedCol === keyNorm) {
    return 0.95
  }
  if (normalizedCol === displayNorm) {
    return 0.9
  }
  if (
    normalizedCol.length >= 3 &&
    (normalizedCol.includes(keyNorm) || keyNorm.includes(normalizedCol))
  ) {
    return 0.6
  }
  return 0
}

function score(profile: ColumnProfile, field: TemplateMappingField): number {
  const name = nameScore(normalize(profile.name), field)
  if (name <= 0) {
    return 0
  }
  const compatible = DATATYPE_COMPAT[profile.inferred_datatype]?.has(field.datatype) ?? false
  return Math.min(1, name + (compatible ? 0.05 : 0))
}

// The mechanical stand-in: a per-column best-effort match. Always returns a valid catalog key
// per column (the target select then always has a matching option); an empty catalog yields a
// null target at confidence 0.
export function mechanicalSuggest(
  columns: ColumnProfile[],
  catalog: TemplateMappingField[],
): MappingSuggestionResponse {
  const suggestions: Suggestion[] = columns.map((profile) => {
    let bestKey: string | null = null
    let bestScore = -1
    for (const field of catalog) {
      const s = score(profile, field)
      if (s > bestScore) {
        bestScore = s
        bestKey = field.key
      }
    }
    return {
      source_column: profile.name,
      suggested_target: bestKey,
      confidence: Math.max(0, bestScore),
    }
  })
  return { source: 'fallback', model: null, suggestions }
}

// POST /api/v1/mapping-suggestions (real) or the mechanical stand-in (fixture). The catalog is
// used only in fixture mode; the real server reads its own field catalog (the request carries
// no catalog, matching the server schema).
export async function getMappingSuggestions(
  request: MappingSuggestionRequest,
  catalog: TemplateMappingField[],
): Promise<MappingSuggestionResponse> {
  if (isRealMode()) {
    return postJson<MappingSuggestionResponse>('/api/v1/mapping-suggestions', request)
  }
  return mechanicalSuggest(request.columns, catalog)
}
