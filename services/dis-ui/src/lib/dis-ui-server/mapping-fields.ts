import { useQuery } from '@tanstack/react-query'

import { getJson } from './client'
import { isRealMode } from './mode'

// Template mapping-fields catalog (T1): the canonical fields a mapping template may
// target. Shaped EXACTLY to the real dis-ui-server contract
// (services/dis-ui-server/src/dis_ui_server/schemas/mapping_fields.py:TemplateMappingField),
// served by GET /api/v1/template-mapping-fields as a bare array, identical for every
// tenant. The real catalog is DERIVED at backend startup from the dis-canonical event
// models (sale_event + change_event) intersected with the mapping-produced provenance
// partition, merged with authored labels. This fixture MIRRORS build_field_catalog()'s
// output as of this commit; the live endpoint is the source of truth at real mode.
//
// Note: this is the EVENT catalog only. It deliberately omits store_id (identity-resolved),
// the hot-table columns (current_retail_price, product_name, product_description), and
// tax_treatment (consumer-injected) - none are mapping-produced event targets.
export type FieldSection = 'sale_event' | 'change_event'

export type FieldDatatype =
  | 'text'
  | 'integer'
  | 'number'
  | 'date'
  | 'datetime'
  | 'boolean'
  | 'choice'
  | 'json'

export type TemplateMappingField = {
  key: string
  display_name: string
  section: FieldSection
  mandatory: boolean
  datatype: FieldDatatype
  description: string
  // choice fields only; absent otherwise (contract: optional)
  allowed_values?: string[]
  // text fields with a declared cap; absent otherwise (contract: optional)
  max_length?: number
}

// The 35 real entries (20 sale_event + 15 change_event), values mirrored from the backend:
// key/section/display_name/description from catalog/labels.py; mandatory from pydantic
// required-ness intersected with the produced partition; datatype/allowed_values/max_length
// from the dis-canonical model annotations. Descriptions are verbatim except em-dashes,
// normalized to keep this repo dash-free (semantics identical).
export const CATALOG_FIXTURE: TemplateMappingField[] = [
  // ---- sale_event (StoreSkuSaleEvent, declaration order) ----
  {
    key: 'event_date',
    display_name: 'Event date',
    section: 'sale_event',
    mandatory: true,
    datatype: 'date',
    description:
      'Calendar date of the sale at UTC, derived from the sale timestamp (use a date_from_datetime derive on the timestamp column).',
  },
  {
    key: 'sku_id',
    display_name: 'SKU',
    section: 'sale_event',
    mandatory: true,
    datatype: 'text',
    description: 'The product identifier as your system reports it.',
    max_length: 128,
  },
  {
    key: 'sku_variant',
    display_name: 'SKU variant',
    section: 'sale_event',
    mandatory: false,
    datatype: 'text',
    description: 'Variant qualifier (size, colour) when your SKUs carry one.',
    max_length: 128,
  },
  {
    key: 'sku_lot_batch',
    display_name: 'Lot / batch',
    section: 'sale_event',
    mandatory: false,
    datatype: 'text',
    description: 'Lot or batch number when tracked.',
    max_length: 128,
  },
  {
    key: 'event_subtype',
    display_name: 'Sale type',
    section: 'sale_event',
    mandatory: true,
    datatype: 'choice',
    description: 'Whether the line is a sale, a return, or a void.',
    allowed_values: ['SALE', 'RETURN', 'VOID'],
  },
  {
    key: 'source_sale_timestamp',
    display_name: 'Sale timestamp',
    section: 'sale_event',
    mandatory: true,
    datatype: 'datetime',
    description:
      "When the sale happened, per your system's clock. Declare the exact format and timezone; they are never guessed.",
  },
  {
    key: 'transaction_id',
    display_name: 'Transaction ID',
    section: 'sale_event',
    mandatory: false,
    datatype: 'text',
    description: 'Your receipt/transaction reference; enables exact correction matching.',
    max_length: 128,
  },
  {
    key: 'line_item_seq',
    display_name: 'Line number',
    section: 'sale_event',
    mandatory: false,
    datatype: 'integer',
    description: 'Position of this line within the transaction.',
  },
  {
    key: 'quantity',
    display_name: 'Quantity',
    section: 'sale_event',
    mandatory: true,
    datatype: 'number',
    description: 'Units sold (negative or RETURN-typed rows represent returns).',
  },
  {
    key: 'unit_retail_price',
    display_name: 'Unit retail price',
    section: 'sale_event',
    mandatory: true,
    datatype: 'number',
    description: 'Listed shelf price per unit at sale time.',
  },
  {
    key: 'unit_sale_price',
    display_name: 'Unit sale price',
    section: 'sale_event',
    mandatory: true,
    datatype: 'number',
    description: 'Price per unit actually charged after discounts.',
  },
  {
    key: 'discount_amount',
    display_name: 'Discount amount',
    section: 'sale_event',
    mandatory: false,
    datatype: 'number',
    description: 'Absolute discount applied to the line.',
  },
  {
    key: 'discount_pct',
    display_name: 'Discount %',
    section: 'sale_event',
    mandatory: false,
    datatype: 'number',
    description: 'Percentage discount applied to the line.',
  },
  {
    key: 'unit_cost',
    display_name: 'Unit cost',
    section: 'sale_event',
    mandatory: false,
    datatype: 'number',
    description: 'Your cost per unit, when the source reports it.',
  },
  {
    key: 'promo_identifier',
    display_name: 'Promotion ID',
    section: 'sale_event',
    mandatory: false,
    datatype: 'text',
    description: 'Identifier of the promotion applied, if any.',
    max_length: 128,
  },
  {
    key: 'tax_amount',
    display_name: 'Tax amount',
    section: 'sale_event',
    mandatory: false,
    datatype: 'number',
    description: 'Tax charged on the line.',
  },
  {
    key: 'currency',
    display_name: 'Currency',
    section: 'sale_event',
    mandatory: true,
    datatype: 'text',
    description:
      'ISO 4217 code (e.g. EUR). If the file has no currency column, provide it as a constant derive.',
    max_length: 3,
  },
  {
    key: 'payment_method',
    display_name: 'Payment method',
    section: 'sale_event',
    mandatory: false,
    datatype: 'text',
    description: 'How the transaction was paid (cash, card, ...).',
    max_length: 64,
  },
  {
    key: 'customer_token',
    display_name: 'Customer token',
    section: 'sale_event',
    mandatory: false,
    datatype: 'text',
    description:
      'Tokenized customer reference. Raw identifiers are tokenized at the receiver before this column is ever populated.',
    max_length: 128,
  },
  {
    key: 'sale_channel',
    display_name: 'Sale channel',
    section: 'sale_event',
    mandatory: false,
    datatype: 'text',
    description: 'Originating channel (in-store, online, ...).',
    max_length: 32,
  },
  // ---- change_event (StoreSkuChangeEvent, declaration order) ----
  {
    key: 'event_date',
    display_name: 'Event date',
    section: 'change_event',
    mandatory: true,
    datatype: 'date',
    description:
      'Calendar date of the change at UTC, derived from the event timestamp (use a date_from_datetime derive on the timestamp column).',
  },
  {
    key: 'sku_id',
    display_name: 'SKU',
    section: 'change_event',
    mandatory: true,
    datatype: 'text',
    description: 'The product identifier as your system reports it.',
    max_length: 128,
  },
  {
    key: 'sku_variant',
    display_name: 'SKU variant',
    section: 'change_event',
    mandatory: false,
    datatype: 'text',
    description: 'Variant qualifier (size, colour) when your SKUs carry one.',
    max_length: 128,
  },
  {
    key: 'sku_lot_batch',
    display_name: 'Lot / batch',
    section: 'change_event',
    mandatory: false,
    datatype: 'text',
    description: 'Lot or batch number when tracked.',
    max_length: 128,
  },
  {
    key: 'event_category',
    display_name: 'Change category',
    section: 'change_event',
    mandatory: true,
    datatype: 'choice',
    description:
      'What kind of change this template carries (inventory, price, cost, ...). Usually a constant derive per template.',
    allowed_values: ['INVENTORY', 'PRICE', 'COST', 'REGULATORY', 'STATUS', 'CATALOGUE', 'OTHER'],
  },
  {
    key: 'event_subtype',
    display_name: 'Change subtype',
    section: 'change_event',
    mandatory: true,
    datatype: 'text',
    description: 'Your finer-grained change label (free-form).',
    max_length: 64,
  },
  {
    key: 'source_event_timestamp',
    display_name: 'Change timestamp',
    section: 'change_event',
    mandatory: true,
    datatype: 'datetime',
    description:
      "When the change happened, per your system's clock. Declare the exact format and timezone; they are never guessed.",
  },
  {
    key: 'effective_from',
    display_name: 'Effective from',
    section: 'change_event',
    mandatory: false,
    datatype: 'datetime',
    description: 'When the new value takes effect, if scheduled.',
  },
  {
    key: 'effective_until',
    display_name: 'Effective until',
    section: 'change_event',
    mandatory: false,
    datatype: 'datetime',
    description: 'When the value expires, if bounded.',
  },
  {
    key: 'attribute_name',
    display_name: 'Attribute',
    section: 'change_event',
    mandatory: false,
    datatype: 'text',
    description:
      'Which attribute changed (e.g. stock_qty, current_retail_price). Usually a constant derive per template.',
    max_length: 64,
  },
  {
    key: 'value_before',
    display_name: 'Value before',
    section: 'change_event',
    mandatory: false,
    datatype: 'json',
    description: "The attribute's value before the change, when the source reports it.",
  },
  {
    key: 'value_after',
    display_name: 'Value after',
    section: 'change_event',
    mandatory: false,
    datatype: 'json',
    description: "The attribute's value after the change.",
  },
  {
    key: 'reason_code',
    display_name: 'Reason code',
    section: 'change_event',
    mandatory: false,
    datatype: 'text',
    description: "Your system's code for why the change happened.",
    max_length: 64,
  },
  {
    key: 'reason_note',
    display_name: 'Reason note',
    section: 'change_event',
    mandatory: false,
    datatype: 'text',
    description: 'Free-text note accompanying the change.',
    max_length: 256,
  },
  {
    key: 'change_context',
    display_name: 'Change context',
    section: 'change_event',
    mandatory: false,
    datatype: 'json',
    description: 'Additional structured context your system attaches to the change.',
  },
]

// GET /api/v1/template-mapping-fields. Tenant-independent. Real mode calls the live
// endpoint; fixture mode returns the inlined catalog as-is.
export async function getTemplateMappingFields(): Promise<TemplateMappingField[]> {
  if (isRealMode()) {
    return getJson<TemplateMappingField[]>('/api/v1/template-mapping-fields')
  }
  return [...CATALOG_FIXTURE]
}

// `enabled` gates the fetch: the bare (no-param) endpoint is rejected by the type-required
// backend (slice-14d), so callers that have no template_type must NOT fire it. Defaults to true
// so existing callers are unchanged; the unified connector route passes the POS-branch predicate.
export function useTemplateMappingFields(enabled = true) {
  return useQuery({
    queryKey: ['dis-ui-server', 'template-mapping-fields'],
    queryFn: getTemplateMappingFields,
    enabled,
    staleTime: Infinity,
    retry: false,
  })
}

// Ordered, de-duped canonical target keys across the catalog (for any flat-list consumer).
export function canonicalTargetKeys(catalog: TemplateMappingField[]): string[] {
  const seen = new Set<string>()
  const keys: string[] = []
  for (const field of catalog) {
    if (!seen.has(field.key)) {
      seen.add(field.key)
      keys.push(field.key)
    }
  }
  return keys
}

// ============================================================================================
// Type-aware catalog (Chunk 2, WIRED): GET /api/v1/template-mapping-fields?template_type=X.
//
// Slice 14d made the catalog TYPE-AWARE: `template_type` is REQUIRED (missing/invalid -> 400
// `invalid_template_type`) and the response is the new UNIFORM 10-key shape (CatalogField),
// shaped EXACTLY to schemas/mapping_fields.py:TemplateMappingField. This is ADDITIVE: the
// legacy no-param `getTemplateMappingFields()` / `TemplateMappingField` above are left intact
// for the existing (soon-retired) CSV onboarding surface; the unified Add Source uses the
// type-aware fetch below. `section` is wider (includes `system`), `datatype` is null only for
// the `__ignore__` sentinel, and `sink` is the canonical table (null for functional/sentinel).
// ============================================================================================

export type TemplateTypeKey = 'sales' | 'inventory_change' | 'snapshot'

export type CatalogFieldSection =
  | 'sale_event'
  | 'change_event'
  | 'identity'
  | 'product'
  | 'pricing'
  | 'inventory'
  | 'expiry'
  | 'regulatory_status'
  | 'system'

// The uniform 10-key wire object. `datatype` is null only for the `__ignore__` sentinel
// (section `system`, sink null); assigning a source column to `__ignore__` is how the Ignore
// toggle is represented in the mapping.
export type CatalogField = {
  key: string
  display_name: string
  section: CatalogFieldSection
  mandatory: boolean
  constraints: string | null
  datatype: FieldDatatype | null
  description: string
  allowed_values: string[] | null
  max_length: number | null
  sink: string | null
}

// The `__ignore__` sentinel appended to every field set (section `system`, datatype/sink null).
export const IGNORE_FIELD: CatalogField = {
  key: '__ignore__',
  display_name: 'Ignore this column',
  section: 'system',
  mandatory: false,
  constraints: null,
  datatype: null,
  description: 'Assign a source column here to exclude it from the canonical template.',
  allowed_values: null,
  max_length: null,
  sink: null,
}

// Widen a legacy event-catalog entry to the new 10-key shape (fixture-only helper).
function toCatalogField(field: TemplateMappingField, sink: string): CatalogField {
  return {
    key: field.key,
    display_name: field.display_name,
    section: field.section,
    mandatory: field.mandatory,
    constraints: null,
    datatype: field.datatype,
    description: field.description,
    allowed_values: field.allowed_values ?? null,
    max_length: field.max_length ?? null,
    sink,
  }
}

// A compact catalogue (current-position) field set for the snapshot type (the legacy fixture is
// event-only). The live endpoint is the source of truth in real mode; this keeps fixture/test
// mode plausible.
const SNAPSHOT_FIELDS: CatalogField[] = [
  {
    key: 'sku_id',
    display_name: 'SKU',
    section: 'identity',
    mandatory: true,
    constraints: null,
    datatype: 'text',
    description: 'The product identifier as your system reports it.',
    allowed_values: null,
    max_length: 128,
    sink: 'store_sku_current_position',
  },
  {
    key: 'current_retail_price',
    display_name: 'Current retail price',
    section: 'pricing',
    mandatory: false,
    constraints: null,
    datatype: 'number',
    description: 'The latest shelf price per unit for this SKU.',
    allowed_values: null,
    max_length: null,
    sink: 'store_sku_current_position',
  },
  {
    key: 'stock_on_hand',
    display_name: 'Stock on hand',
    section: 'inventory',
    mandatory: false,
    constraints: null,
    datatype: 'number',
    description: 'Current on-hand quantity for this SKU.',
    allowed_values: null,
    max_length: null,
    sink: 'store_sku_current_position',
  },
  {
    key: 'currency',
    display_name: 'Currency',
    section: 'pricing',
    mandatory: false,
    constraints: null,
    datatype: 'text',
    description: 'ISO 4217 code; provide as a constant derive if the file has no column.',
    allowed_values: null,
    max_length: 3,
    sink: 'store_sku_current_position',
  },
  IGNORE_FIELD,
]

// Per-type fixtures, MIRRORING the type-aware endpoint output as of this commit. `sales` and
// `inventory_change` are derived from the legacy event catalog; `snapshot` is authored above.
const CATALOG_FIXTURE_BY_TYPE: Record<string, CatalogField[]> = {
  sales: [
    ...CATALOG_FIXTURE.filter((f) => f.section === 'sale_event').map((f) =>
      toCatalogField(f, 'store_sku_sale_event'),
    ),
    IGNORE_FIELD,
  ],
  inventory_change: [
    ...CATALOG_FIXTURE.filter((f) => f.section === 'change_event').map((f) =>
      toCatalogField(f, 'store_sku_change_event'),
    ),
    IGNORE_FIELD,
  ],
  snapshot: SNAPSHOT_FIELDS,
}

// GET /api/v1/template-mapping-fields?template_type=X. `template_type` is REQUIRED by the
// backend (missing/invalid -> 400 invalid_template_type). Real mode calls the live endpoint;
// fixture mode returns the per-type inlined catalog.
export async function getTemplateMappingFieldsForType(
  templateType: string,
): Promise<CatalogField[]> {
  if (isRealMode()) {
    return getJson<CatalogField[]>(
      `/api/v1/template-mapping-fields?template_type=${encodeURIComponent(templateType)}`,
    )
  }
  return [...(CATALOG_FIXTURE_BY_TYPE[templateType] ?? [IGNORE_FIELD])]
}

// Enabled only once a template type is chosen (the param is required). queryKey carries the
// type so switching types refetches the right field set.
export function useTemplateMappingFieldsForType(templateType: string | null) {
  return useQuery({
    queryKey: ['dis-ui-server', 'template-mapping-fields', templateType ?? 'none'],
    queryFn: () => getTemplateMappingFieldsForType(templateType as string),
    enabled: templateType !== null && templateType.length > 0,
    staleTime: Infinity,
    retry: false,
  })
}
