import { ArrowRight } from 'lucide-react'
import type { Dispatch } from 'react'

import { Select } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { ErrorState } from '../../components/states/ErrorState'
import { LoadingState } from '../../components/states/LoadingState'
import { StatusBadge } from '../../components/StatusBadge'
import type { StatusTone } from '../../components/StatusBadge'
import {
  DATE_FORMAT_CHOICES,
  DECIMAL_CHOICES,
  requiredRuleKind,
} from '../../components/locale-rules'
import type { LocaleDeclaration, RuleKind } from '../../components/locale-rules'
import type { FieldDatatype } from '../../lib/dis-ui-server/mapping-fields'
import {
  CSV_DATETIME_FORMATS,
  LOCALE_PRESETS,
  localePreset,
} from '../../lib/dis-ui-server/connectors-api'
import type {
  ConnectorMappingField,
  ConnectorMappingResponse,
  LocaleKey,
} from '../../lib/dis-ui-server/connectors-api'
import { isIgnored, mappingTargetFor } from './state'
import type { ConnectorWizardAction, ConnectorWizardState } from './state'

// Step (AI mapping), shared by both branches: per-field rows - source field -> canonical select
// (pre-selected from the suggestion), a confidence chip (High/Medium/Low) + a "why" reasoning
// line, a detected FORMAT line, and a per-row IGNORE checkbox (= assign to the catalog's
// `__ignore__` field, which is filtered out of the dropdown below).
//
// CHUNK 2: the canonical-target grouping is now SECTION-AGNOSTIC (driven by whatever sections the
// catalog carries) so it serves both the POS fixture catalog (sale_event/change_event) and the
// CSV type-aware catalog (identity/product/pricing/... per template_type). The `catalog` prop is
// a NARROW structural type so both the legacy `TemplateMappingField[]` (POS) and the new
// `CatalogField[]` (CSV) are assignable without coupling to either concrete shape.
//
// This step owns its row + format-line rendering directly (it once mirrored the now-retired
// MappingReview surface). Confidence/reasoning/format are STUBBED (TODO-wire-to-Vertex in
// connectors-api).

// The minimal field shape the mapping step needs from a catalog. Both the legacy event catalog
// and the type-aware CatalogField satisfy this structurally.
export type MappingTargetField = {
  key: string
  display_name: string
  section: string
  mandatory: boolean
  datatype: FieldDatatype | null
}

// Known section labels; unknown sections humanize their key. `system` is never shown (it holds
// the `__ignore__` sentinel, represented by the Ignore checkbox, not a selectable target).
const SECTION_LABEL: Record<string, string> = {
  sale_event: 'Sale event',
  change_event: 'Change event',
  identity: 'Identity',
  product: 'Product',
  pricing: 'Pricing',
  inventory: 'Inventory',
  expiry: 'Expiry',
  regulatory_status: 'Regulatory status',
}

function humanizeSection(section: string): string {
  return SECTION_LABEL[section] ?? section.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}

function isSelectableTarget(field: MappingTargetField): boolean {
  return field.section !== 'system' && field.key !== '__ignore__'
}

// Confidence -> a High/Medium/Low chip in a semantic tone (presentation only).
function confidenceBand(confidence: number): { text: string; tone: StatusTone } {
  if (confidence >= 0.8) {
    return { text: 'High', tone: 'success' }
  }
  if (confidence >= 0.5) {
    return { text: 'Medium', tone: 'warning' }
  }
  return { text: 'Low', tone: 'danger' }
}

// A human description of the detected format, built from the SAME locale-rules choices the
// CSV path uses (so the surfaces read identically). null kind => no conversion needed.
function describeFormat(kind: RuleKind, decl: LocaleDeclaration | null): string {
  if (kind === null) {
    return 'No format conversion needed'
  }
  if (decl === null) {
    return 'Format not detected; declare it before going live'
  }
  if (kind === 'decimal') {
    const choice = DECIMAL_CHOICES.find((c) => c.value === decl.decimal_separator)
    const thousands = decl.thousands_separator ? `, thousands "${decl.thousands_separator}"` : ''
    return choice ? `${choice.label}${thousands}` : 'Decimal separator detected'
  }
  // date / datetime
  const choice = DATE_FORMAT_CHOICES.find((c) => c.value === decl.format)
  const tz = kind === 'datetime' && decl.timezone ? ` in ${decl.timezone}` : ''
  return choice ? `${choice.example}${tz}` : 'Date format detected'
}

export function MappingStep({
  state,
  dispatch,
  mapping,
  catalog,
  loading,
  formatDeclarations = false,
  error = null,
  onRetry,
}: {
  state: ConnectorWizardState
  dispatch: Dispatch<ConnectorWizardAction>
  mapping: ConnectorMappingResponse | null
  catalog: MappingTargetField[]
  loading: boolean
  // CSV branch only: show the source-format declaration controls (locale picker + per-column
  // datetime format / percentage), assembled into the 16a create body. POS leaves it false and
  // keeps the read-only "Detected format" line.
  formatDeclarations?: boolean
  // CSV branch only: a parse / suggestions / catalog failure, shown with a retry instead of the
  // perpetual loading spinner (mapping stays null on failure otherwise). POS leaves it null.
  error?: string | null
  onRetry?: () => void
}) {
  if (error !== null) {
    return <ErrorState message={error} onRetry={onRetry} />
  }
  if (loading || mapping === null) {
    return (
      <LoadingState
        label={
          formatDeclarations
            ? 'Reading your file and suggesting field mappings...'
            : 'Suggesting field mappings...'
        }
      />
    )
  }

  // Catalog datatype lookup by key (first occurrence wins; datatype is consistent per key).
  const catalogByKey = new Map<string, MappingTargetField>()
  for (const field of catalog) {
    if (!catalogByKey.has(field.key)) {
      catalogByKey.set(field.key, field)
    }
  }

  // Section-grouped canonical options, derived from whatever sections the catalog carries (in
  // first-seen order), excluding the `system`/`__ignore__` sentinel.
  function renderCanonicalGroups() {
    const sections: string[] = []
    for (const field of catalog) {
      if (isSelectableTarget(field) && !sections.includes(field.section)) {
        sections.push(field.section)
      }
    }
    return sections.map((section) => (
      <optgroup key={section} label={humanizeSection(section)}>
        {catalog
          .filter((field) => isSelectableTarget(field) && field.section === section)
          .map((field) => (
            <option key={`${section}-${field.key}`} value={field.key}>
              {field.display_name}
              {field.mandatory ? ' *' : ''}
              {field.datatype !== null ? ` (${field.datatype})` : ''}
            </option>
          ))}
      </optgroup>
    ))
  }

  // CSV-only (formatDeclarations): the per-column source-format declaration controls, by the
  // TARGET datatype. datetime/date -> a date-format select (16a "DD-MM-YYYY" vocab); number ->
  // a "values are percentages" checkbox (decimal/thousand come from the template-level locale).
  function renderFormatDeclaration(field: ConnectorMappingField, datatype: FieldDatatype | null) {
    const col = state.csvColumnFormat[field.sourceField]
    if (datatype === 'datetime' || datatype === 'date') {
      return (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-caption text-muted-foreground">Source date format</span>
          <Select
            aria-label={`Date format for ${field.sourceField}`}
            value={col?.datetimeFormat ?? ''}
            onChange={(e) =>
              dispatch({
                type: 'setCsvDatetimeFormat',
                sourceField: field.sourceField,
                format: e.target.value,
              })
            }
            className="h-8 w-auto"
          >
            <option value="">Declare format...</option>
            {CSV_DATETIME_FORMATS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </Select>
        </div>
      )
    }
    if (datatype === 'number') {
      return (
        <label className="flex items-center gap-2 text-caption text-muted-foreground">
          <input
            type="checkbox"
            aria-label={`Values are percentages for ${field.sourceField}`}
            checked={col?.isPercentage ?? false}
            onChange={(e) =>
              dispatch({
                type: 'setCsvPercentage',
                sourceField: field.sourceField,
                isPercentage: e.target.checked,
              })
            }
          />
          Values are percentages (numbers parsed as {localePreset(state.csvLocale).label})
        </label>
      )
    }
    return null
  }

  function row(field: ConnectorMappingField) {
    const target = mappingTargetFor(state, field)
    const ignored = isIgnored(state, field.sourceField)
    const band = confidenceBand(field.confidence)
    // The locale rule the MAPPED field's datatype implies (recomputes with the target).
    const datatype = catalogByKey.get(target)?.datatype ?? null
    const kind: RuleKind = datatype === null ? null : requiredRuleKind(datatype)
    return (
      <div
        key={field.sourceField}
        className={cn(
          'flex flex-col gap-2.5 rounded-md border border-border px-4 py-3.5',
          ignored && 'opacity-50',
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="font-mono text-sm text-foreground">{field.sourceField}</span>
          <ArrowRight aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
          <Select
            aria-label={`Canonical for ${field.sourceField}`}
            value={target}
            disabled={ignored}
            onChange={(e) =>
              dispatch({
                type: 'setMappingTarget',
                sourceField: field.sourceField,
                target: e.target.value,
              })
            }
            className="h-8 w-auto"
          >
            <option value="">Unmapped</option>
            {field.alternatives.length > 0 ? (
              <optgroup label="Assistant's alternatives">
                {field.alternatives.map((alt) => (
                  <option key={`alt-${alt}`} value={alt}>
                    {alt}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {renderCanonicalGroups()}
          </Select>
          {ignored ? (
            <StatusBadge tone="neutral">Ignored</StatusBadge>
          ) : (
            // TODO-wire-to-Vertex: confidence is a placeholder from the stub suggestion.
            <StatusBadge tone={band.tone}>
              {Math.round(field.confidence * 100)}% {band.text}
            </StatusBadge>
          )}
          <label className="ml-auto flex items-center gap-1 text-caption text-muted-foreground">
            <input
              type="checkbox"
              aria-label={`Ignore ${field.sourceField}`}
              checked={ignored}
              onChange={() => dispatch({ type: 'toggleIgnore', sourceField: field.sourceField })}
            />
            Ignore
          </label>
        </div>

        {!ignored ? (
          <>
            {field.reasoning != null && field.reasoning.length > 0 ? (
              <div className="text-body text-muted-foreground italic">Why: {field.reasoning}</div>
            ) : null}
            {formatDeclarations ? (
              // CSV: the operator DECLARES source format (server returns none); assembled into
              // the 16a create body. Shown only for datatypes that need it.
              renderFormatDeclaration(field, datatype)
            ) : (
              <div className="text-body text-muted-foreground">
                Detected format: {describeFormat(kind, field.detectedFormat)}
              </div>
            )}
          </>
        ) : (
          <div className="text-body text-muted-foreground">
            Excluded from the canonical template (assigned to __ignore__).
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* TODO-wire-to-Vertex: source label mirrors MappingSuggestionResponse.source. */}
        {mapping.source === 'vertex' ? (
          <StatusBadge tone="info">Suggestions: AI</StatusBadge>
        ) : (
          <StatusBadge tone="neutral">Suggestions: basic match</StatusBadge>
        )}
        {/* CSV: template-level number locale (decimal/thousand separators). Built for the full
            target set (US/EU/Swiss); EU dot-thousands 422s until 16b (see connectors-api). */}
        {formatDeclarations ? (
          <div className="flex items-center gap-2">
            <Label htmlFor="csv-number-locale" className="text-caption text-muted-foreground">
              Number locale
            </Label>
            <Select
              id="csv-number-locale"
              value={state.csvLocale}
              onChange={(e) =>
                dispatch({ type: 'setCsvLocale', locale: e.target.value as LocaleKey })
              }
              className="h-8 w-auto"
            >
              {LOCALE_PRESETS.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
      </div>
      <div className="flex flex-col gap-3">{mapping.fields.map((field) => row(field))}</div>
    </div>
  )
}
