import type { Dispatch } from 'react'

import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { StatusBadge } from '../../components/StatusBadge'
import type { AtlasField, AtlasTable } from '../../lib/dis-ui-server/atlas'
import {
  effectiveNaturalKey,
  enumCandidateFor,
  mandatoryFor,
  nullableFor,
  originFor,
  piiFor,
} from './ratify-state'
import type { RatifyAction, RatifyState } from './ratify-state'

// The Atlas ratify grid (A4 PR3b), following the MappingStep layout: per-field rows with the
// A4-specific controls (nullable / mandatory / PII / enum-candidate edits + the natural-key
// membership editor) and an origin marker (inferred vs ratified). Driven by the ratify-state
// reducer (edits hold only uncommitted changes; the server draft is passed in).
//
// The wire model carries no per-field `section`, so rows are grouped by the edit-legality
// boundary instead: MAPPING-PRODUCED fields are editable; SYSTEM fields (produced_by other than
// mapping_produced) are locked read-only, mirroring the BFF section-4 lock (_apply_patch refuses
// a non-mapping_produced edit).
//
// readOnly renders the whole grid as pure display (no Select/Input/checkbox, no natural-key
// toggles) for the published-schema detail: "published is immutable" is visible in the UI, not
// only enforced by the PR2 trigger.

const MAPPING_PRODUCED = 'mapping_produced'

function isEditable(field: AtlasField, readOnly: boolean): boolean {
  return !readOnly && field.produced_by === MAPPING_PRODUCED
}

function originBadge(origin: AtlasField['origin']) {
  if (origin === 'human') {
    return <StatusBadge tone="success">Ratified</StatusBadge>
  }
  if (origin === 'inferred') {
    return <StatusBadge tone="warning">Inferred</StatusBadge>
  }
  return <StatusBadge tone="neutral">System</StatusBadge>
}

function yesNo(value: boolean): string {
  return value ? 'Yes' : 'No'
}

export function RatifyGrid({
  table,
  state,
  dispatch,
  readOnly = false,
}: {
  table: AtlasTable
  state: RatifyState
  // Required unless readOnly; the published detail passes a no-op-free read-only grid.
  dispatch?: Dispatch<RatifyAction>
  readOnly?: boolean
}) {
  const naturalKey = effectiveNaturalKey(state, table)

  function toggleNaturalKey(name: string, member: boolean): void {
    if (dispatch === undefined) {
      return
    }
    const next = member ? [...naturalKey, name] : naturalKey.filter((k) => k !== name)
    dispatch({ type: 'setNaturalKey', key: next })
  }

  function row(field: AtlasField) {
    const editable = isEditable(field, readOnly)
    const origin = originFor(state, field)
    const nullable = nullableFor(state, field)
    const mandatory = mandatoryFor(state, field)
    const pii = piiFor(state, field)
    const enumCandidate = enumCandidateFor(state, field)
    const inKey = naturalKey.includes(field.name)
    return (
      <div
        key={field.name}
        data-slot="ratify-row"
        data-field={field.name}
        className={cn(
          'flex flex-col gap-2.5 rounded-md border border-border px-4 py-3.5',
          !editable && !readOnly && 'opacity-70',
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="font-mono text-sm text-foreground">{field.name}</span>
          <span className="text-caption text-muted-foreground">{field.type_ref}</span>
          {originBadge(origin)}
          {field.curated_bearing ? <StatusBadge tone="info">Needs ratification</StatusBadge> : null}
          {!editable && !readOnly ? (
            <span className="text-caption text-muted-foreground">Locked (system field)</span>
          ) : null}
        </div>

        {editable ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-caption text-muted-foreground">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                aria-label={`Nullable for ${field.name}`}
                checked={nullable}
                onChange={(e) =>
                  dispatch?.({
                    type: 'setFieldNullable',
                    name: field.name,
                    value: e.target.checked,
                  })
                }
              />
              Nullable
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                aria-label={`Mandatory for ${field.name}`}
                checked={mandatory}
                onChange={(e) =>
                  dispatch?.({
                    type: 'setFieldMandatory',
                    name: field.name,
                    value: e.target.checked,
                  })
                }
              />
              Mandatory
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                aria-label={`Contains PII for ${field.name}`}
                checked={pii !== null && pii !== 'none'}
                // The wire pii vocab is open; curated_bearing treats any non-null, non-"none"
                // value as PII, so toggle between null and a marker string.
                onChange={(e) =>
                  dispatch?.({
                    type: 'setFieldPii',
                    name: field.name,
                    value: e.target.checked ? 'pii' : null,
                  })
                }
              />
              Contains PII
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                aria-label={`Natural key member ${field.name}`}
                checked={inKey}
                onChange={(e) => toggleNaturalKey(field.name, e.target.checked)}
              />
              Natural key
            </label>
            <span className="flex items-center gap-1.5">
              Enum values
              <Input
                aria-label={`Enum candidates for ${field.name}`}
                value={enumCandidate.join(', ')}
                placeholder="comma-separated"
                className="h-7 w-48"
                onChange={(e) =>
                  dispatch?.({
                    type: 'setFieldEnumCandidate',
                    name: field.name,
                    values: e.target.value
                      .split(',')
                      .map((v) => v.trim())
                      .filter((v) => v.length > 0),
                  })
                }
              />
            </span>
          </div>
        ) : (
          // Read-only display (system fields, or the whole grid in readOnly mode). No controls.
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-muted-foreground">
            <span>Nullable: {yesNo(nullable)}</span>
            <span>Mandatory: {yesNo(mandatory)}</span>
            <span>PII: {pii !== null && pii !== 'none' ? 'yes' : 'no'}</span>
            {inKey ? <span>Natural key member</span> : null}
            {enumCandidate.length > 0 ? <span>Enum: {enumCandidate.join(', ')}</span> : null}
          </div>
        )}
      </div>
    )
  }

  const editableFields = table.fields.filter((f) => f.produced_by === MAPPING_PRODUCED)
  const systemFields = table.fields.filter((f) => f.produced_by !== MAPPING_PRODUCED)

  return (
    <div className="flex flex-col gap-6">
      {readOnly ? (
        <StatusBadge tone="neutral">Published and immutable, read-only</StatusBadge>
      ) : null}

      <div className="flex flex-col gap-3">
        <h3 className="text-label text-muted-foreground">Inferred fields</h3>
        {editableFields.map((field) => row(field))}
      </div>

      {systemFields.length > 0 ? (
        <div className="flex flex-col gap-3">
          <h3 className="text-label text-muted-foreground">System fields (locked)</h3>
          {systemFields.map((field) => row(field))}
        </div>
      ) : null}
    </div>
  )
}
