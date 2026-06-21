import { buildNormalizeSpec } from '../../components/locale-rules'
import type { LocaleDeclaration, RuleKind } from '../../components/locale-rules'
import type { SourceMappingRules } from '../dis-ui-server/mapping-templates'

// Assemble the full mapping_rules document from the wizard's resolved per-column state
// (create/promote flow). The output is the D49 SourceMapping shape with EXACTLY the five keys
// {version, rename, normalize, cast, derive}; the server model is frozen + extra="forbid"
// (libs/dis-mapping/.../source_mapping.py), so no extra key may appear. cast/derive are empty
// for now (the wizard collects no cast/derive UI; a cast need downstream is a flagged
// follow-up). normalize is keyed by the CANONICAL target and built per column via
// buildNormalizeSpec (which emits parse_decimal with thousands_separator: null when undeclared,
// never omitted - the key must be present, transform.py:149-169).

export type AssemblyColumn = {
  source_col: string
  // The effective canonical target for this column ('' means "do not map" -> skipped).
  proposed_canonical: string
  // The mandatory locale rule this column's MAPPED field requires (null = none).
  rule_kind: RuleKind
  // The operator's locale declaration for this column (undefined when no rule).
  locale: LocaleDeclaration | undefined
}

export type AssemblyResult =
  | { ok: true; rules: SourceMappingRules }
  | { ok: false; error: string }

// Build {version, rename, normalize, cast, derive} or return a validation error. The only
// pre-submit validation here is rename-target uniqueness (two source columns mapping to one
// canonical field would silently collide; the server also rejects this). Locale completeness
// is already enforced by the Continue gate, so buildNormalizeSpec returns non-null for any
// rule-bearing column by the time Go-live runs.
export function assembleMappingRules(columns: AssemblyColumn[]): AssemblyResult {
  const rename: Record<string, string> = {}
  const normalize: Record<string, { op: string; args: Record<string, unknown> }[]> = {}
  const targetToSource = new Map<string, string>()

  for (const column of columns) {
    const canonical = column.proposed_canonical
    if (canonical === '') {
      continue // "do not map" -> omit from rename entirely
    }
    const existing = targetToSource.get(canonical)
    if (existing !== undefined) {
      return {
        ok: false,
        error: `Two columns map to "${canonical}" (${existing} and ${column.source_col}); each canonical field takes one column.`,
      }
    }
    targetToSource.set(canonical, column.source_col)
    rename[column.source_col] = canonical

    if (column.rule_kind !== null) {
      const spec = buildNormalizeSpec(column.rule_kind, column.locale)
      if (spec !== null) {
        // Keyed by the canonical target, matching the server's normalize shape.
        normalize[canonical] = [spec]
      }
    }
  }

  // Exactly the five keys (FM2: the server model is extra="forbid").
  return {
    ok: true,
    rules: { version: 1, rename, normalize, cast: {}, derive: {} },
  }
}
