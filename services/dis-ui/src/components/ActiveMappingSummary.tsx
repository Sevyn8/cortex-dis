import { ArrowRight } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { MappingTemplateVersion, NormalizeOp } from '../lib/dis-ui-server/mapping-templates'
import type { CatalogField } from '../lib/dis-ui-server/mapping-fields'

// Render one normalize/derive op as "name (arg=value, ...)" - the format declarations
// (date_format, decimal_separator) live in the args.
function formatOp(op: NormalizeOp): string {
  const args = Object.entries(op.args).map(([key, value]) => `${key}=${String(value)}`)
  return args.length > 0 ? `${op.op} (${args.join(', ')})` : op.op
}

// Read-only view of a mapping version, split into the two concerns the UI keeps distinct:
// FIELD mappings (source column -> canonical field, enriched from the T1 catalog) and
// FORMAT rules (normalize/cast/derive: date format, decimal separator, type cast). Purely
// presentational and read-only (no inputs/selects), so it is safe to reuse anywhere the
// active mapping must be SHOWN but never edited: the template detail (T2) and the
// recurring-batch upload flow (T4, reuse-not-re-map).
//
// T8: rendered as compact WRAPPING lines (not wide tables), so the cards never scroll
// horizontally (FM2). Each field mapping is one "source -> canonical" line; each format
// rule is one "field: rule" line.
export function ActiveMappingSummary({
  version,
  catalog,
}: {
  version: MappingTemplateVersion
  catalog: CatalogField[]
}) {
  const fieldByKey = new Map<string, CatalogField>()
  for (const field of catalog) {
    if (!fieldByKey.has(field.key)) {
      fieldByKey.set(field.key, field)
    }
  }

  const rules = version.mapping_rules
  const renameEntries = Object.entries(rules.rename)
  // Format rules: union of the canonical columns touched by normalize / cast / derive.
  const ruleColumns = [
    ...new Set([
      ...Object.keys(rules.normalize),
      ...Object.keys(rules.cast),
      ...Object.keys(rules.derive),
    ]),
  ]

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Field mappings</CardTitle>
          <p className="text-caption text-muted-foreground">
            Which canonical field each source column maps to (active v{version.version}).
          </p>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-3">
            {renameEntries.map(([sourceCol, canonicalKey]) => {
              const field = fieldByKey.get(canonicalKey)
              return (
                <li key={sourceCol} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="font-mono text-xs break-all text-muted-foreground">
                    {sourceCol}
                  </span>
                  <ArrowRight
                    aria-hidden="true"
                    className="size-3.5 shrink-0 text-muted-foreground"
                  />
                  <span className="font-medium break-all text-foreground">
                    {field ? field.display_name : canonicalKey}
                    {field?.mandatory ? ' *' : ''}
                  </span>
                  <span className="font-mono text-caption break-all text-muted-foreground">
                    {canonicalKey}
                  </span>
                  <span className="text-caption text-muted-foreground">
                    {field?.section ?? '-'}
                  </span>
                  <span aria-hidden="true" className="text-caption text-muted-foreground">
                    ·
                  </span>
                  <span className="text-caption text-muted-foreground">
                    {field?.datatype ?? '-'}
                  </span>
                </li>
              )
            })}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Format rules</CardTitle>
          <p className="text-caption text-muted-foreground">
            How values are normalized and cast (date format, decimal separator, type).
          </p>
        </CardHeader>
        <CardContent>
          {ruleColumns.length === 0 ? (
            <p className="text-caption text-muted-foreground">No format rules declared.</p>
          ) : (
            <ul className="flex flex-col gap-3">
              {ruleColumns.map((col) => {
                const parts: string[] = []
                for (const op of rules.normalize[col] ?? []) {
                  parts.push(`normalize: ${formatOp(op)}`)
                }
                const castSpec = rules.cast[col]
                if (castSpec) {
                  const detailStr =
                    castSpec.precision !== undefined
                      ? `${castSpec.type}(${castSpec.precision},${castSpec.scale ?? 0})`
                      : castSpec.type
                  parts.push(`cast: ${detailStr}`)
                }
                for (const op of rules.derive[col] ?? []) {
                  parts.push(`derive: ${formatOp(op)}`)
                }
                return (
                  <li key={col} className="flex flex-col gap-0.5">
                    <span className="font-mono text-xs break-all text-foreground">{col}</span>
                    <span className="text-caption break-words text-muted-foreground">
                      {parts.join('; ')}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
