import type { LocaleDeclaration } from '../../components/locale-rules'
import { parseCsvFile } from '../onboarding/analyze-csv'
import { postJson } from './client'
import { isRealMode } from './mode'
import { getMappingSuggestions } from './mapping-suggestions'
import type { CatalogField, FieldDatatype, TemplateMappingField } from './mapping-fields'
import type { ConnectorKey } from './connectors-catalog'

// =====================================================================================
// Connectors API seam for the "Connect a System" surface.
//
// MIXED now (Chunk 3): the CSV branch's analyze + create are REAL (they compose existing,
// deployed endpoints); the POS branch and the CSV preview stay stubbed (no confirmed
// contract). Real paths go through lib/dis-ui-server/* (the only allowed backend boundary):
//   - analyzeCsvSample: client-side parse (papaparse, analyze-csv.ts) + POST /mapping-suggestions
//     (type-aware, D90) for per-column targets/confidence/reasoning.
//   - createCsvTemplate: POST /mapping-templates with the Slice-16a semantic columns[] shape
//     (D89). 16a returns a SYNTHETIC 201 (nothing persisted until 16c); the UI reads it honestly.
// Still STUBBED with TODO(wire): the POS authorize/locations/suggest/create, and the CSV
// preview rows. detectedFormat is no longer server-provided; the operator DECLARES format in
// the mapping step (locale picker + per-column datetime format), assembled into the 16a body.
// =====================================================================================

// ----- OAuth (recommended path) ------------------------------------------------------

export type OAuthInitiation = {
  // The provider authorize URL the browser would be sent to. Stubbed string for now.
  authorizeUrl: string
  // Opaque CSRF/correlation state echoed back on the callback.
  state: string
}

// TODO(wire): POST /api/v1/connectors/{connector}/oauth/initiate with the pre-auth field
// (shop domain / region). Server returns the provider authorize URL + state; the browser
// redirects there and the provider calls our callback. Sevyn8 app credentials are held
// server-side and never reach the client.
export function initiateOAuth(
  connector: ConnectorKey,
  preAuth: Record<string, string>,
): Promise<OAuthInitiation> {
  return Promise.resolve({
    authorizeUrl: `https://stub.invalid/oauth/${connector}?shop=${encodeURIComponent(
      preAuth.shop_domain ?? preAuth.region ?? '',
    )}`,
    state: `stub-state-${connector}`,
  })
}

export type ConnectorAccount = {
  // The provider account / business name we connected to.
  businessName: string
  // Provider-side account identifier.
  accountId: string
  // We request read-only scopes only.
  readOnly: boolean
  // The access token is stored server-side (never returned to the client).
  tokenStored: boolean
}

// TODO(wire): the OAuth callback hits the server, which exchanges the code for a token,
// stores it server-side, and returns the connected account summary. The client polls or is
// redirected back with the result; this function stands in for "fetch the exchanged account".
export function exchangeToken(
  connector: ConnectorKey,
  params: { state: string },
): Promise<ConnectorAccount> {
  void params
  return Promise.resolve(stubAccount(connector))
}

// ----- API-token path ----------------------------------------------------------------

// TODO(wire): POST /api/v1/connectors/{connector}/token with the per-connector token fields.
// Server validates the token against the provider (read-only), stores it server-side, and
// returns the connected account summary. The raw token is never persisted client-side.
export function submitApiToken(
  connector: ConnectorKey,
  tokenFields: Record<string, string>,
): Promise<ConnectorAccount> {
  void tokenFields
  return Promise.resolve(stubAccount(connector))
}

function stubAccount(connector: ConnectorKey): ConnectorAccount {
  const names: Record<ConnectorKey, string> = {
    shopify: 'Acme Retail (Shopify)',
    square: 'Acme Retail (Square)',
    clover: 'Acme Retail (Clover)',
  }
  return {
    businessName: names[connector],
    accountId: `acct_stub_${connector}`,
    readOnly: true,
    tokenStored: true,
  }
}

// ----- Locations ----------------------------------------------------------------------

export type ConnectorLocation = {
  // Provider-side location id.
  id: string
  // Human label for the location.
  name: string
  // A short address line for disambiguation.
  address: string
}

// TODO(wire): GET /api/v1/connectors/{connector}/locations for the connected account. The
// returned provider locations are mapped to DIS store_id in this step. The location ->
// store_id resolution touches identity/store resolution and is a Sanjeev spec item.
export function fetchLocations(connector: ConnectorKey): Promise<ConnectorLocation[]> {
  void connector
  return Promise.resolve([
    { id: 'loc_001', name: 'Downtown Flagship', address: '100 Market St' },
    { id: 'loc_002', name: 'Riverside Mall', address: '24 Riverside Ave' },
    { id: 'loc_003', name: 'Airport Kiosk', address: 'Terminal B, Gate 12' },
  ])
}

// ----- Data types + cadence -----------------------------------------------------------

export type ConnectorDataType = 'orders' | 'products' | 'inventory'

export type SyncCadence = 'realtime' | 'hourly' | 'daily'

export const SYNC_CADENCES: { value: SyncCadence; label: string }[] = [
  { value: 'realtime', label: 'Real time (webhooks)' },
  { value: 'hourly', label: 'Every hour' },
  { value: 'daily', label: 'Once a day' },
]

// ----- AI mapping suggestions ---------------------------------------------------------

export type ConnectorMappingSource = 'vertex' | 'fallback'

// One source field's suggestion, shaped like the real mapping-suggestion response
// (suggested target + alternatives), enriched with the per-row signals the surface shows.
export type ConnectorMappingField = {
  sourceField: string
  // Canonical catalog key, or null. Mirrors MappingSuggestionResponse.Suggestion.suggested_target.
  suggestedTarget: string | null
  // Catalog keys, mirrors Suggestion.alternatives.
  alternatives: string[]
  // TODO(wire): Vertex mapping-suggestion confidence (0..1). Placeholder values for now.
  confidence: number
  // TODO(wire): Vertex mapping-suggestion reasoning ("why" line). Placeholder for now.
  reasoning: string | null
  // Detected FORMAT line. Reuses the EXISTING locale/format mechanism (LocaleDeclaration),
  // not a new detected-format API. null = no locale rule applies to this field's datatype.
  // TODO(wire): the real detected format would come from the same source-shape inference the
  // CSV path uses; here it is a pre-filled stub.
  detectedFormat: LocaleDeclaration | null
  // A couple of sample values for context.
  sampleValues: string[]
}

export type ConnectorMappingResponse = {
  // TODO(wire): mirror MappingSuggestionResponse.source ("llm"/"fallback"); "vertex" here is
  // the placeholder label for the LLM path.
  source: ConnectorMappingSource
  fields: ConnectorMappingField[]
}

// TODO(wire): POST /api/v1/connectors/{connector}/mapping-suggestions with the selected data
// types. Server returns per-field Vertex suggestions (target + alternatives + confidence +
// reasoning) plus the detected source-shape format. This stub returns a fixed orders-shaped
// suggestion set whose targets are real catalog keys (so the canonical select always matches).
export function fetchMappingSuggestions(
  connector: ConnectorKey,
  dataTypes: ConnectorDataType[],
): Promise<ConnectorMappingResponse> {
  void connector
  void dataTypes
  return Promise.resolve({
    source: 'vertex',
    fields: [
      {
        sourceField: 'order_id',
        suggestedTarget: 'transaction_id',
        alternatives: ['line_item_seq'],
        confidence: 0.97,
        reasoning: 'Field name and sample values match a transaction reference.',
        detectedFormat: null,
        sampleValues: ['1001', '1002'],
      },
      {
        sourceField: 'line_sku',
        suggestedTarget: 'sku_id',
        alternatives: ['sku_variant'],
        confidence: 0.95,
        reasoning: 'Column name "sku" maps directly to the canonical SKU identifier.',
        detectedFormat: null,
        sampleValues: ['TSHIRT-RED-M', 'MUG-001'],
      },
      {
        sourceField: 'quantity',
        suggestedTarget: 'quantity',
        alternatives: [],
        confidence: 0.92,
        reasoning: 'Exact name match to canonical quantity.',
        detectedFormat: { decimal_separator: '.', thousands_separator: '' },
        sampleValues: ['2', '1'],
      },
      {
        sourceField: 'unit_price',
        suggestedTarget: 'unit_sale_price',
        alternatives: ['unit_retail_price'],
        confidence: 0.74,
        reasoning: 'Likely the price charged per unit; confirm against retail price.',
        detectedFormat: { decimal_separator: '.', thousands_separator: ',' },
        sampleValues: ['1,299.50', '19.00'],
      },
      {
        sourceField: 'created_at',
        suggestedTarget: 'source_sale_timestamp',
        alternatives: [],
        confidence: 0.88,
        reasoning: 'Order creation time aligns with the sale timestamp.',
        detectedFormat: { format: '%Y-%m-%d', timezone: 'America/New_York' },
        sampleValues: ['2026-05-30', '2026-05-31'],
      },
      {
        sourceField: 'currency_code',
        suggestedTarget: 'currency',
        alternatives: [],
        confidence: 0.6,
        reasoning: 'Three-letter code resembles an ISO 4217 currency.',
        detectedFormat: null,
        sampleValues: ['USD', 'USD'],
      },
      {
        sourceField: 'gateway',
        suggestedTarget: null,
        alternatives: ['payment_method'],
        confidence: 0.32,
        reasoning: 'No confident canonical target; review or ignore this field.',
        detectedFormat: null,
        sampleValues: ['stripe', 'cash'],
      },
    ],
  })
}

// ----- Template type ------------------------------------------------------------------

export type ConnectorTemplateType = { value: string; label: string }

// TODO(wire): the template type is backend-provided (derived from the connected data types /
// canonical event partition). GET /api/v1/connectors/{connector}/template-type. Stubbed value.
export function fetchTemplateType(): Promise<ConnectorTemplateType> {
  return Promise.resolve({ value: 'sale_event', label: 'Sale event' })
}

// ----- Preview ------------------------------------------------------------------------

// TODO(wire): the server would return canonical-coerced preview rows for the proposed
// mapping (the authoritative coercion). This stub returns fixed rows keyed by canonical
// field; the surface drops ignored fields before rendering.
export function fetchPreviewRows(): Promise<Record<string, string>[]> {
  return Promise.resolve([
    {
      transaction_id: '1001',
      sku_id: 'TSHIRT-RED-M',
      quantity: '2',
      unit_sale_price: '1299.50',
      source_sale_timestamp: '2026-05-30T00:00:00-04:00',
      currency: 'USD',
    },
    {
      transaction_id: '1002',
      sku_id: 'MUG-001',
      quantity: '1',
      unit_sale_price: '19.00',
      source_sale_timestamp: '2026-05-31T00:00:00-04:00',
      currency: 'USD',
    },
  ])
}

// ----- Create the live source ---------------------------------------------------------

export type LiveConnectorSource = {
  id: string
  connector: ConnectorKey
  sourceName: string
  status: 'live' | 'paused'
  // ISO timestamp of the last sync, or null if it has not run yet.
  lastSyncAt: string | null
  recordsSynced: number
}

export type CreateConnectorSourceInput = {
  connector: ConnectorKey
  sourceName: string
  authMethod: 'oauth' | 'api_token'
  locationIds: string[]
  dataTypes: ConnectorDataType[]
  cadence: SyncCadence
  // Canonical keys the operator chose to ignore (excluded from the template).
  ignoredFields: string[]
  templateType: string
}

// TODO(wire): POST /api/v1/connectors with the assembled mapping template + sync config.
// Server creates the connector source (ACTIVE), kicks off the first sync, and returns the
// live source summary. Stubbed here.
export function createConnectorSource(
  input: CreateConnectorSourceInput,
): Promise<LiveConnectorSource> {
  return Promise.resolve({
    id: `src_stub_${input.connector}`,
    connector: input.connector,
    sourceName: input.sourceName,
    status: 'live',
    lastSyncAt: null,
    recordsSynced: 0,
  })
}

// =====================================================================================
// CSV / SFTP branch stubs (Chunk 2). These three remain STUBBED because their contracts are
// NOT confirmed in Sanjeev's probe doc. The canonical TARGETS for the CSV mapping step are
// fetched FOR REAL via template-mapping-fields?template_type=X (mapping-fields.ts); only the
// upload/analysis, the create, and the preview are stubbed. Each marked TODO(wire).
// =====================================================================================

// REAL (D90): parse the uploaded file client-side (papaparse, analyze-csv.ts) into a column
// profile, then call the type-aware /mapping-suggestions endpoint PASSING `template_type` so
// the suggested targets come from the SAME per-type catalog the mapping step's dropdown uses.
// `detectedFormat` is null on purpose: the server returns no format, so the operator DECLARES
// it in the mapping step (locale picker + per-column datetime format), which createCsvTemplate
// assembles into the 16a `src_*` declarations. `catalog` is used ONLY by the fixture-mode
// mechanical matcher (real mode ignores it and the server reads its own per-type catalog).
export async function analyzeCsvSample(
  file: File,
  templateType: string,
  catalog: CatalogField[],
): Promise<ConnectorMappingResponse> {
  const parsed = await parseCsvFile(file)
  // Adapt the type-aware CatalogField[] to the legacy TemplateMappingField shape the fixture
  // matcher reads, dropping the __ignore__/system sentinel (datatype null). The matcher scores
  // on key/display_name/datatype only; section is irrelevant, so the cast is safe.
  const fixtureCatalog: TemplateMappingField[] = catalog
    .filter((f) => f.section !== 'system' && f.key !== '__ignore__' && f.datatype !== null)
    .map((f) => ({
      key: f.key,
      display_name: f.display_name,
      section: f.section as TemplateMappingField['section'],
      mandatory: f.mandatory,
      datatype: f.datatype as FieldDatatype,
      description: f.description,
      allowed_values: f.allowed_values ?? undefined,
      max_length: f.max_length ?? undefined,
    }))
  const resp = await getMappingSuggestions(
    { columns: parsed.columns, template_type: templateType },
    fixtureCatalog,
  )
  const byColumn = new Map(parsed.columns.map((c) => [c.name, c]))
  const fields: ConnectorMappingField[] = resp.suggestions.map((s) => ({
    sourceField: s.source_column,
    suggestedTarget: s.suggested_target,
    alternatives: s.alternatives ?? [],
    confidence: s.confidence,
    reasoning: s.reasoning ?? null,
    detectedFormat: null, // server returns no format; declared client-side (locale picker)
    sampleValues: byColumn.get(s.source_column)?.sample_values ?? [],
  }))
  // MappingSuggestionResponse.source is "llm"/"fallback"; map "llm" -> the surface's "vertex".
  return { source: resp.source === 'llm' ? 'vertex' : 'fallback', fields }
}

// ----- Locale picker (build-ahead, full target set) -----------------------------------
// US / EU / Swiss decimal+thousand presets. KNOWN GAP: as shipped in 16a, the create endpoint
// accepts src_thousand_separator ONLY in {",", "'"} (NOT "."), so the EU dot-thousands preset
// 422s until Sanjeev's 16b. We build the picker for ALL THREE locales anyway (per the brief);
// the type below intentionally allows "." so the EU preset compiles and is offered.
export type LocaleKey = 'us' | 'eu' | 'swiss'
export type LocalePreset = {
  key: LocaleKey
  label: string
  decimal: '.' | ','
  thousand: '.' | ',' | "'"
}
export const LOCALE_PRESETS: LocalePreset[] = [
  { key: 'us', label: 'US (1,299.50)', decimal: '.', thousand: ',' },
  { key: 'eu', label: 'EU (1.299,50)', decimal: ',', thousand: '.' }, // thousand "." 422s until 16b
  { key: 'swiss', label: "Swiss (1'299.50)", decimal: '.', thousand: "'" },
]
export function localePreset(key: LocaleKey): LocalePreset {
  return LOCALE_PRESETS.find((p) => p.key === key) ?? LOCALE_PRESETS[0]
}

// Per-datetime-column format choices. The `value` is the wire token sent verbatim as
// src_datetime_format in the create columns[] body (a READABLE token, never a strptime code):
// Sanjeev's slice-16c translation layer converts the token to the engine format and REJECTS
// any token outside the locked five with a 4xx. This set is held in EXACT lockstep with that
// backend set (DD-MM-YYYY, DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD, DD-MM-YY); labels are friendly,
// only the value is load-bearing.
export const CSV_DATETIME_FORMATS: { value: string; label: string }[] = [
  { value: 'DD-MM-YYYY', label: 'Day-Month-Year (31-12-2025)' },
  { value: 'DD/MM/YYYY', label: 'Day/Month/Year (31/12/2025)' },
  { value: 'MM/DD/YYYY', label: 'Month/Day/Year (12/31/2025)' },
  { value: 'YYYY-MM-DD', label: 'Year-Month-Day (2025-12-31)' },
  { value: 'DD-MM-YY', label: 'Day-Month-Year, 2-digit year (31-12-25)' },
]

// ----- Create (Slice-16a semantic columns[] contract, D89) ----------------------------

// One source-to-destination column declaration, mirroring the backend MappingColumn (16a).
// src_thousand_separator allows "." (the EU preset) even though 16a only accepts {",", "'"};
// EU dot-thousands therefore 422s until 16b (deliberate, see LOCALE_PRESETS).
export type ConnectorColumn = {
  src_key: string
  dest_key: string // catalog key for the chosen template_type, or "__ignore__"
  src_datetime_format?: string | null
  src_decimal_separator?: '.' | ',' | null
  src_thousand_separator?: '.' | ',' | "'" | null
  src_is_percentage?: boolean | null
}

export type CreateCsvTemplateInput = {
  // PROVISIONAL: slugified from the source name as a stopgap (collision/quality risk); a
  // dedicated source_id field or a source registry comes later.
  sourceId: string
  templateName: string
  templateType: string
  columns: ConnectorColumn[]
}

// The synthetic-201 reality (16a): a real fresh template_id but NOTHING persisted, no rules
// assembled (draft v1, no active, mapping_version_id 0) until 16c. We read the response
// gracefully - never hard-assume active_version - so the Created UI can be honest.
export type CreatedTemplate = {
  templateId: string
  templateName: string
  templateType: string
  activeVersion: number | null
  draftVersion: number | null
}

type RawCreateResponse = {
  template_id: string
  template_name: string
  template_type: string
  active_version: number | null
  draft_version: number | null
}

// REAL (D89): POST /api/v1/mapping-templates with the semantic columns[] body (NO mapping_rules
// - it is extra-forbidden and would 422). 16a shape-validates + returns a SYNTHETIC 201. Fixture
// mode mirrors that synthetic shape (draft v1, no active). The create persists nothing until
// 16c, so callers must present the result honestly (submitted, not live/listable/ingestible).
export async function createCsvTemplate(input: CreateCsvTemplateInput): Promise<CreatedTemplate> {
  if (isRealMode()) {
    const raw = await postJson<RawCreateResponse>('/api/v1/mapping-templates', {
      source_id: input.sourceId,
      template_name: input.templateName,
      template_type: input.templateType,
      columns: input.columns,
    })
    return {
      templateId: raw.template_id,
      templateName: raw.template_name,
      templateType: raw.template_type,
      activeVersion: raw.active_version,
      draftVersion: raw.draft_version,
    }
  }
  // Fixture: mirror the slice-16c REAL create (create-as-ACTIVE, D88): the row is written ACTIVE
  // and persisted, so the response carries active_version 1 (no draft). Keeps dev/tests in step
  // with real behavior, so CsvCreatedStep shows "Created and live" consistently.
  return {
    templateId: 'tmpl_stub_csv',
    templateName: input.templateName,
    templateType: input.templateType,
    activeVersion: 1,
    draftVersion: null,
  }
}

// TODO(wire): the client-side preview shape is not re-confirmed for this surface. The real flow
// would coerce the parsed sample rows through the assembled mapping (the server pipeline is the
// authoritative coercion). This stub returns fixed canonical-keyed rows; the surface drops
// ignored targets before rendering.
export function fetchCsvPreviewRows(): Promise<Record<string, string>[]> {
  return Promise.resolve([
    {
      sku_id: 'TSHIRT-RED-M',
      quantity: '2',
      unit_sale_price: '1299.50',
      source_sale_timestamp: '2025-12-31T00:00:00+00:00',
      transaction_id: 'R-1001',
      event_subtype: 'SALE',
    },
    {
      sku_id: 'MUG-001',
      quantity: '1',
      unit_sale_price: '19.00',
      source_sale_timestamp: '2026-01-01T00:00:00+00:00',
      transaction_id: 'R-1002',
      event_subtype: 'RETURN',
    },
  ])
}
