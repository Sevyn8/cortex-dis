import { useQuery } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { getJson, patchJson } from './client'
import { isRealMode } from './mode'

// Mapping-template endpoints (slice 14b, D68): a mapping is a TEMPLATE - a version lineage
// per (tenant, source, template_id). Shaped EXACTLY to the real contracts
// (services/dis-ui-server/.../schemas/mapping_templates.py): list MappingTemplate at
// GET /api/v1/mapping-templates[?source_id=], detail MappingTemplateDetail at
// GET /api/v1/mapping-templates/{template_id} (throw-style 404 for unknown), create at
// POST /api/v1/mapping-templates, edit at PATCH /api/v1/mapping-templates/{template_id}.
// Mode-aware (T10): real mode calls the live endpoints; fixture mode (default) returns the
// inlined fixtures, so local dev + tests work with no backend.
//
// Contract diff (T10): the real MappingTemplate/Detail wire shape has NO `ingestion_mode`
// (it is a UI-only provisional field, T8/D-flag: the real model would derive it from the
// source connector type). The real branch defaults it to 'file' (today's only real grain is
// file/CSV), keeping the UI type stable; the fixture carries explicit values.

export type TemplateStatus = 'draft' | 'staged' | 'active' | 'deprecated'

// Ingestion mode (T8): how data arrives for the source this template belongs to. 'file'
// sources (CSV upload) accept a manual batch upload; 'api' sources (POS/ERP connectors like
// Square) sync automatically, so there is no manual ingest action.
//
// PROVISIONAL + ISOLATED (FM4): the real contract does not expose this yet. In the real
// model the mode derives from the source's connector type (config.source_mappings / the
// connector registry), and the mapping-templates list response would denormalize it per
// template (or the UI would join sources). Kept here, on the template fixture, so the UI can
// gate the ingest affordance now; swap to the contract field when it lands.
export type IngestionMode = 'file' | 'api'

// Raw D49 SourceMapping (mapping_rules served raw by the backend): the FIELD half is
// `rename` (source col -> canonical key); the FORMAT-RULES half is normalize/cast/derive
// (date format, decimal separator, type casts) - the two concerns T2 lays out separately.
// Real TransformSpec shape (libs/dis-mapping/models/transform.py): { op, args } with args
// NESTED. parse_date/parse_datetime use args.format (a polars strptime string) + (datetime)
// args.timezone; parse_decimal uses args.decimal_separator + args.thousands_separator.
export type NormalizeOp = { op: string; args: Record<string, unknown> }
export type CastSpec = { type: string; precision?: number; scale?: number } // CastType: string|integer|decimal|date|datetime|boolean
export type SourceMappingRules = {
  version: number
  rename: Record<string, string>
  normalize: Record<string, NormalizeOp[]>
  cast: Record<string, CastSpec>
  derive: Record<string, NormalizeOp[]>
}

export type MappingTemplateVersion = {
  mapping_version_id: number // global BIGSERIAL (D22 pin / audit ref)
  version: number // per-template version_seq
  status: TemplateStatus
  mapping_rules: SourceMappingRules
  field_count: number
  transform_count: number
  predecessor_version_id: number | null
  created_at: string
  created_by_user_id: string | null // raw UUID or null (Blocker 5 / D56 pending)
  activated_at: string | null
  deprecated_at: string | null
}

export type MappingTemplate = {
  template_id: string // UUID, lowercase string
  source_id: string
  template_name: string
  // The packet axis (Slice 14d): 'sales' | 'inventory_change' | 'snapshot'. ADDITIVE + OPTIONAL
  // + READ-ONLY: surfaced from the real GET when present so the registry can show a friendly
  // template-type label (resolved via GET /template-types display_name). The create/patch
  // bodies do NOT carry it here - the create-path template_type wiring is a separate, pending
  // change (Sanjeev's POST contract). Legacy/absent -> undefined (the UI degrades, no badge).
  template_type?: string
  ingestion_mode: IngestionMode // T8, provisional (see IngestionMode)
  latest_version: number
  active_version: number | null
  staged_version: number | null
  draft_version: number | null
  versions_count: number
  created_at: string
  latest_version_created_at: string
}

export type MappingTemplateDetail = MappingTemplate & {
  versions: MappingTemplateVersion[]
}

// Create/edit request bodies (T10), shaped to the real contracts
// (schemas/mapping_templates.py:MappingTemplateCreate / MappingTemplatePatch). `mapping_rules`
// travels as the raw D49 document (= SourceMappingRules). PATCH carries at least one field.
export type MappingTemplateCreate = {
  source_id: string
  template_name: string
  mapping_rules: SourceMappingRules
}
export type MappingTemplatePatch = {
  template_name?: string
  mapping_rules?: SourceMappingRules
}

// The real wire shapes OMIT the UI-only `ingestion_mode` (see header). Type the raw response
// with it OPTIONAL so the real branch can default it; the fixture carries it explicitly.
type RawMappingTemplate = Omit<MappingTemplate, 'ingestion_mode'> & {
  ingestion_mode?: IngestionMode
}
type RawMappingTemplateDetail = Omit<MappingTemplateDetail, 'ingestion_mode'> & {
  ingestion_mode?: IngestionMode
}

// SEED, keyed by tenant. manual_csv_upload carries two templates (the D68 multi-template
// story: a source can carry sales / inventory / pricing). Rename targets are real T1
// catalog keys ONLY - store_id is identity-resolved and never a field-mapping target.
const SALES_TEMPLATE_ID = '0190ac10-5a00-7000-8a00-0000000000a1'
const INVENTORY_TEMPLATE_ID = '0190ac10-5a00-7000-8a00-0000000000a2'
// A draft-only template (never activated): no active version, so a recurring batch cannot
// reuse it. Exercises the active-version precondition (T4 FM3).
const PRICING_TEMPLATE_ID = '0190ac10-5a00-7000-8a00-0000000000a3'
// A template under a SECOND source, so the flat "Ingest Data" list (T5) spans more than one
// source and source context is meaningful (two templates can share a name across sources).
const ORDERS_TEMPLATE_ID = '0190ac10-5a00-7000-8a00-0000000000b1'

const MAPPING_TEMPLATE_FIXTURES: Record<string, MappingTemplateDetail[]> = {
  t_acme9k2l1mn4: [
    {
      template_id: SALES_TEMPLATE_ID,
      source_id: 'manual_csv_upload',
      template_name: 'Sales',
      template_type: 'sales',
      ingestion_mode: 'file',
      latest_version: 3,
      active_version: 2,
      staged_version: 3,
      draft_version: null,
      versions_count: 3,
      created_at: '2026-04-10T09:00:00Z',
      latest_version_created_at: '2026-06-02T09:00:00Z',
      versions: [
        {
          mapping_version_id: 41,
          version: 3,
          status: 'staged',
          field_count: 6,
          transform_count: 4,
          predecessor_version_id: 38,
          created_at: '2026-06-02T09:00:00Z',
          created_by_user_id: null,
          activated_at: null,
          deprecated_at: null,
          mapping_rules: {
            version: 1,
            rename: {
              item_code: 'sku_id',
              qty: 'quantity',
              txn_date: 'source_sale_timestamp',
              unit_price: 'unit_sale_price',
              ccy: 'currency',
              sale_type: 'event_subtype',
            },
            normalize: {
              source_sale_timestamp: [
                { op: 'parse_datetime', args: { format: '%d-%m-%Y %H:%M:%S', timezone: 'UTC' } },
              ],
              unit_sale_price: [
                { op: 'parse_decimal', args: { decimal_separator: ',', thousands_separator: '.' } },
              ],
            },
            cast: {
              quantity: { type: 'integer' },
              unit_sale_price: { type: 'decimal', precision: 12, scale: 4 },
            },
            derive: {
              event_date: [{ op: 'date_from_datetime', args: { source: 'source_sale_timestamp' } }],
            },
          },
        },
        {
          mapping_version_id: 38,
          version: 2,
          status: 'active',
          field_count: 5,
          transform_count: 3,
          predecessor_version_id: 22,
          created_at: '2026-05-28T09:00:00Z',
          created_by_user_id: null,
          activated_at: '2026-05-28T09:05:00Z',
          deprecated_at: null,
          mapping_rules: {
            version: 1,
            rename: {
              item_code: 'sku_id',
              qty: 'quantity',
              txn_date: 'source_sale_timestamp',
              unit_price: 'unit_sale_price',
              sale_type: 'event_subtype',
            },
            normalize: {
              source_sale_timestamp: [
                { op: 'parse_datetime', args: { format: '%d-%m-%Y %H:%M:%S', timezone: 'UTC' } },
              ],
              unit_sale_price: [
                { op: 'parse_decimal', args: { decimal_separator: ',', thousands_separator: '.' } },
              ],
            },
            cast: {
              quantity: { type: 'integer' },
              unit_sale_price: { type: 'decimal', precision: 12, scale: 4 },
            },
            derive: {
              event_date: [{ op: 'date_from_datetime', args: { source: 'source_sale_timestamp' } }],
            },
          },
        },
        {
          mapping_version_id: 22,
          version: 1,
          status: 'deprecated',
          field_count: 3,
          transform_count: 1,
          predecessor_version_id: null,
          created_at: '2026-04-10T09:00:00Z',
          created_by_user_id: null,
          activated_at: '2026-04-10T09:05:00Z',
          deprecated_at: '2026-05-28T09:05:00Z',
          mapping_rules: {
            version: 1,
            rename: { item_code: 'sku_id', qty: 'quantity', txn_date: 'source_sale_timestamp' },
            normalize: {
              source_sale_timestamp: [
                { op: 'parse_datetime', args: { format: '%d-%m-%Y %H:%M:%S', timezone: 'UTC' } },
              ],
            },
            cast: { quantity: { type: 'integer' } },
            derive: {},
          },
        },
      ],
    },
    {
      template_id: INVENTORY_TEMPLATE_ID,
      source_id: 'manual_csv_upload',
      template_name: 'Inventory',
      template_type: 'inventory_change',
      ingestion_mode: 'file',
      latest_version: 1,
      active_version: 1,
      staged_version: null,
      draft_version: null,
      versions_count: 1,
      created_at: '2026-05-30T09:00:00Z',
      latest_version_created_at: '2026-05-30T09:00:00Z',
      versions: [
        {
          mapping_version_id: 40,
          version: 1,
          status: 'active',
          field_count: 4,
          transform_count: 2,
          predecessor_version_id: null,
          created_at: '2026-05-30T09:00:00Z',
          created_by_user_id: null,
          activated_at: '2026-05-30T09:05:00Z',
          deprecated_at: null,
          mapping_rules: {
            version: 1,
            rename: {
              item_code: 'sku_id',
              change_ts: 'source_event_timestamp',
              attr: 'attribute_name',
            },
            normalize: {
              source_event_timestamp: [
                { op: 'parse_datetime', args: { format: '%Y-%m-%d %H:%M:%S', timezone: 'UTC' } },
              ],
            },
            cast: {},
            derive: {
              event_date: [
                { op: 'date_from_datetime', args: { source: 'source_event_timestamp' } },
              ],
              event_category: [{ op: 'constant', args: { value: 'INVENTORY' } }],
            },
          },
        },
      ],
    },
    {
      template_id: PRICING_TEMPLATE_ID,
      source_id: 'manual_csv_upload',
      template_name: 'Pricing',
      // template_type intentionally OMITTED (legacy/missing): exercises the registry's graceful
      // degrade - no template-type badge, no crash.
      ingestion_mode: 'file',
      latest_version: 1,
      active_version: null,
      staged_version: null,
      draft_version: 1,
      versions_count: 1,
      created_at: '2026-06-04T09:00:00Z',
      latest_version_created_at: '2026-06-04T09:00:00Z',
      versions: [
        {
          mapping_version_id: 44,
          version: 1,
          status: 'draft',
          field_count: 2,
          transform_count: 0,
          predecessor_version_id: null,
          created_at: '2026-06-04T09:00:00Z',
          created_by_user_id: null,
          activated_at: null,
          deprecated_at: null,
          mapping_rules: {
            version: 1,
            rename: { item_code: 'sku_id' },
            normalize: {},
            cast: {},
            derive: {},
          },
        },
      ],
    },
    {
      template_id: ORDERS_TEMPLATE_ID,
      source_id: 'square_pos',
      template_name: 'Orders',
      template_type: 'sales',
      ingestion_mode: 'api',
      latest_version: 1,
      active_version: 1,
      staged_version: null,
      draft_version: null,
      versions_count: 1,
      created_at: '2026-05-29T09:00:00Z',
      latest_version_created_at: '2026-05-29T09:00:00Z',
      versions: [
        {
          mapping_version_id: 51,
          version: 1,
          status: 'active',
          field_count: 4,
          transform_count: 2,
          predecessor_version_id: null,
          created_at: '2026-05-29T09:00:00Z',
          created_by_user_id: null,
          activated_at: '2026-05-29T09:05:00Z',
          deprecated_at: null,
          mapping_rules: {
            version: 1,
            rename: {
              sku: 'sku_id',
              sold_at: 'source_sale_timestamp',
              qty: 'quantity',
              price: 'unit_sale_price',
            },
            normalize: {
              source_sale_timestamp: [
                { op: 'parse_datetime', args: { format: '%Y-%m-%dT%H:%M:%S', timezone: 'UTC' } },
              ],
              unit_sale_price: [
                { op: 'parse_decimal', args: { decimal_separator: '.', thousands_separator: ',' } },
              ],
            },
            cast: { quantity: { type: 'integer' } },
            derive: {},
          },
        },
      ],
    },
  ],
}

function tenantTemplates(snapshot: AuthSnapshot): MappingTemplateDetail[] {
  return MAPPING_TEMPLATE_FIXTURES[snapshot.tenantId ?? ''] ?? []
}

// Default the UI-only ingestion_mode when the real wire omits it (contract diff, header).
function normalizeSummary(raw: RawMappingTemplate): MappingTemplate {
  return { ...raw, ingestion_mode: raw.ingestion_mode ?? 'file' }
}
function normalizeDetail(raw: RawMappingTemplateDetail): MappingTemplateDetail {
  return { ...raw, ingestion_mode: raw.ingestion_mode ?? 'file' }
}

// Fixture-mode synthesis (T10): build a plausible v1 detail from a create/edit request. No
// mutable store - real mode is the source of truth for writes. `live` mirrors the backend
// lifecycle: create writes the v1 ACTIVE (create-as-ACTIVE, D88), edit writes a DRAFT.
function synthV1Detail(
  templateId: string,
  sourceId: string,
  templateName: string,
  rules: SourceMappingRules,
  live: boolean,
): MappingTemplateDetail {
  const createdAt = '2026-06-06T00:00:00Z'
  const version: MappingTemplateVersion = {
    mapping_version_id: 9001,
    version: 1,
    status: live ? 'active' : 'draft',
    mapping_rules: rules,
    field_count: Object.keys(rules.rename).length,
    transform_count:
      Object.values(rules.normalize).reduce((n, specs) => n + specs.length, 0) +
      Object.keys(rules.cast).length +
      Object.values(rules.derive).reduce((n, specs) => n + specs.length, 0),
    predecessor_version_id: null,
    created_at: createdAt,
    created_by_user_id: null,
    activated_at: live ? createdAt : null,
    deprecated_at: null,
  }
  return {
    template_id: templateId,
    source_id: sourceId,
    template_name: templateName,
    ingestion_mode: 'file',
    latest_version: 1,
    active_version: live ? 1 : null,
    staged_version: null,
    draft_version: live ? null : 1,
    versions_count: 1,
    created_at: createdAt,
    latest_version_created_at: createdAt,
    versions: [version],
  }
}

const EMPTY_RULES: SourceMappingRules = {
  version: 1,
  rename: {},
  normalize: {},
  cast: {},
  derive: {},
}

function toSummary(detail: MappingTemplateDetail): MappingTemplate {
  // The list endpoint returns the lineage summary (no versions[]).
  return {
    template_id: detail.template_id,
    source_id: detail.source_id,
    template_name: detail.template_name,
    template_type: detail.template_type,
    ingestion_mode: detail.ingestion_mode,
    latest_version: detail.latest_version,
    active_version: detail.active_version,
    staged_version: detail.staged_version,
    draft_version: detail.draft_version,
    versions_count: detail.versions_count,
    created_at: detail.created_at,
    latest_version_created_at: detail.latest_version_created_at,
  }
}

// GET /api/v1/mapping-templates[?source_id=] -> lineage summaries (own-tenant only). Real
// mode calls the live endpoint (tenant scoped server-side); fixture mode filters the seed.
export async function getMappingTemplates(
  snapshot: AuthSnapshot,
  sourceId?: string,
): Promise<MappingTemplate[]> {
  if (isRealMode()) {
    const query = sourceId === undefined ? '' : `?source_id=${encodeURIComponent(sourceId)}`
    const raw = await getJson<RawMappingTemplate[]>(`/api/v1/mapping-templates${query}`)
    return raw.map(normalizeSummary)
  }
  return tenantTemplates(snapshot)
    .filter((t) => sourceId === undefined || t.source_id === sourceId)
    .map(toSummary)
}

// GET /api/v1/mapping-templates/{template_id} -> detail. Throw-style 404 for unknown (real:
// getJson throws DisUiServerHttpError; fixture: throws). The screen renders an error state.
export async function getMappingTemplate(
  snapshot: AuthSnapshot,
  templateId: string,
): Promise<MappingTemplateDetail> {
  if (isRealMode()) {
    const raw = await getJson<RawMappingTemplateDetail>(
      `/api/v1/mapping-templates/${encodeURIComponent(templateId)}`,
    )
    return normalizeDetail(raw)
  }
  const found = tenantTemplates(snapshot).find((t) => t.template_id === templateId)
  if (found === undefined) {
    throw new Error(`mapping template ${templateId} not found`)
  }
  return found
}

// PATCH /api/v1/mapping-templates/{template_id} -> MappingTemplateDetail (DRAFT edit / new
// DRAFT, per the D17 lifecycle, server-side). Additive (T10): no screen consumer yet. Real
// mode patches; fixture synthesizes a DRAFT echoing the patch.
export async function patchMappingTemplate(
  templateId: string,
  body: MappingTemplatePatch,
): Promise<MappingTemplateDetail> {
  if (isRealMode()) {
    return normalizeDetail(
      await patchJson<RawMappingTemplateDetail>(
        `/api/v1/mapping-templates/${encodeURIComponent(templateId)}`,
        body,
      ),
    )
  }
  return synthV1Detail(
    templateId,
    'manual_csv_upload',
    body.template_name ?? 'Template',
    body.mapping_rules ?? EMPTY_RULES,
    false, // edit writes a DRAFT (the D17 lifecycle for changes)
  )
}

// Activation is no longer a separate step: create-as-ACTIVE (D88) writes the v1 ACTIVE in one
// step, so go-live is immediately live and the draft -> activate ceremony was removed. Any future
// activate-a-new-version-in-an-existing-lineage path lives on dis-ui-server (and must deprecate
// the prior active in one transaction); the UI does not call a separate /activate endpoint.

const TEMPLATES_KEY = ['dis-ui-server', 'mapping-templates'] as const

export function useMappingTemplates(snapshot: AuthSnapshot | null, sourceId: string | null) {
  return useQuery({
    queryKey: [...TEMPLATES_KEY, snapshot?.tenantId ?? 'none', sourceId ?? 'all'],
    queryFn: () => getMappingTemplates(snapshot as AuthSnapshot, sourceId ?? undefined),
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}

export function useMappingTemplate(snapshot: AuthSnapshot | null, templateId: string | null) {
  return useQuery({
    queryKey: [...TEMPLATES_KEY, 'detail', snapshot?.tenantId ?? 'none', templateId ?? 'none'],
    queryFn: () => getMappingTemplate(snapshot as AuthSnapshot, templateId as string),
    enabled: snapshot !== null && templateId !== null,
    staleTime: Infinity,
    retry: false,
  })
}

// The currently-active version of a template, if any (what recurring batches reuse).
export function activeTemplateVersion(
  detail: MappingTemplateDetail,
): MappingTemplateVersion | null {
  return detail.versions.find((v) => v.status === 'active') ?? null
}
