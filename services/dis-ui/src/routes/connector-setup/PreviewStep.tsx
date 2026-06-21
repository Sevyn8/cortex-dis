import type { Dispatch } from 'react'

import { Card, CardContent } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { LoadingState } from '../../components/states/LoadingState'
import { StatusBadge } from '../../components/StatusBadge'
import type { ConnectorMappingField } from '../../lib/dis-ui-server/connectors-api'
import { isIgnored, mappingTargetFor } from './state'
import type { ConnectorWizardAction, ConnectorWizardState } from './state'
import type { WizardErrorCopy } from './wizard-errors'

// Step 7 (Preview): the canonical preview table (STUBBED coerced rows) plus a "Template type"
// field at the top (backend-provided, STUBBED value, TODO-wire). Ignored fields are listed and
// their canonical columns are dropped from the preview. The validation note is NON-BLOCKING.

// TODO(wire): the template-type options are backend-provided (derived from the connected data
// types / canonical event partition). Stubbed list mirroring the catalog's two sections.
const TEMPLATE_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'sale_event', label: 'Sale event' },
  { value: 'change_event', label: 'Change event' },
]

export function PreviewStep({
  state,
  dispatch,
  mappingFields,
  templateType,
  rows,
  loading,
  readOnlyTemplateType = false,
  createError = null,
}: {
  state: ConnectorWizardState
  dispatch: Dispatch<ConnectorWizardAction>
  mappingFields: ConnectorMappingField[]
  templateType: string
  rows: Record<string, string>[]
  loading: boolean
  // CSV branch: the template type was chosen on its own step, so show it read-only here (no
  // re-pick). POS branch leaves it as the editable select.
  readOnlyTemplateType?: boolean
  // A failed create (the semantic gate or a conflict), surfaced inline so the user keeps wizard
  // state and can go back to fix the mapping or change the type. Cleared on retry / Back.
  createError?: WizardErrorCopy | null
}) {
  if (loading) {
    return <LoadingState label="Building preview..." />
  }

  // Canonical targets the operator chose to ignore (excluded columns), and the ignored source
  // fields (for the "what is ignored" summary).
  const ignoredFields = mappingFields.filter((f) => isIgnored(state, f.sourceField))
  const ignoredTargets = new Set(
    ignoredFields.map((f) => mappingTargetFor(state, f)).filter((t) => t.length > 0),
  )

  const columns = Object.keys(rows[0] ?? {}).filter((key) => !ignoredTargets.has(key))

  return (
    <div className="flex flex-col gap-5">
      {createError !== null ? (
        <div
          role="alert"
          className="flex flex-col gap-1 rounded-md border border-danger/40 bg-danger/10 p-4"
        >
          <p className="text-body-strong text-danger">We could not create this template</p>
          <p className="text-body text-foreground">{createError.reason}</p>
          <p className="text-caption text-muted-foreground">{createError.action}</p>
        </div>
      ) : null}

      <div className="flex max-w-xs flex-col gap-1.5">
        <Label htmlFor="connector-template-type">Template type</Label>
        {readOnlyTemplateType ? (
          <>
            <span
              id="connector-template-type"
              className="text-body font-medium text-foreground"
              data-slot="template-type-readonly"
            >
              {templateType}
            </span>
            <span className="text-caption text-muted-foreground">
              Chosen earlier; not editable here.
            </span>
          </>
        ) : (
          <>
            <Select
              id="connector-template-type"
              value={templateType}
              onChange={(e) => dispatch({ type: 'setTemplateType', value: e.target.value })}
            >
              {TEMPLATE_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
            <span className="text-caption text-muted-foreground">
              Backend-provided (stubbed for now).
            </span>
          </>
        )}
      </div>

      {ignoredFields.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-caption text-muted-foreground">Ignored fields</span>
          {ignoredFields.map((f) => (
            <StatusBadge key={f.sourceField} tone="neutral">
              {f.sourceField}
            </StatusBadge>
          ))}
        </div>
      ) : null}

      <Card>
        <CardContent>
          <div className="overflow-x-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  {columns.map((key) => (
                    <TableHead key={key}>{key}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r, index) => (
                  <TableRow key={index}>
                    {columns.map((key) => (
                      <TableCell key={key}>{r[key] ?? ''}</TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <p className="text-body text-muted-foreground">
        This is a sample of how your data maps to the canonical schema. Validation runs again on the
        live sync; this preview does not block going live.
      </p>
    </div>
  )
}
