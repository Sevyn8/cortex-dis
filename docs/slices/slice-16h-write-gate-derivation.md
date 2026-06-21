# Slice 16h: Write-Gate Derivation Refactor

## Depends on

Slice 5b (enrichment lib; the enrichment-produced field set the write gate unions in so
enrichment-supplied columns stay satisfied, D95) and the create-time mapping-validation
derivation that exists after the 16-series create-template work (16a/16c). No new schema.
This is the first of three related slices: 16h (this, derivation refactor), 16i (mandatory-flag
correctness), 16j (nullable columns).

## Goal

Remove the hardcoded write-time completeness set in the streaming consumer and derive it from
the canonical model, the same way the create-time gate already does, so model/DB nullability is
the single source of truth. After this, a future NOT NULL <-> NULLABLE change is a model edit
with no gate code change.

This is a pure refactor: it must produce identical COMPLETE/INCOMPLETE verdicts to the current
hardcoded set. No behavior change.

## Background

Two checks today answer "which columns must a mapping produce?":
- Create-time gate (dis-ui-server): derived from the canonical model (required fields intersected
  with mapping-produced columns).
- Write-time gate (streaming consumer): a hardcoded literal.

Two sources of truth that can drift. Grounding
(docs/scratch/nullable-canonical-columns-grounding.md, the write-gate addendum) found the two
sets produce identical verdicts today and that the consumer already imports the model and a
mapping-produced helper from the shared dis-validation lib. Plan mode confirms these findings
against live code rather than treating them as settled.

## Task

- Promote the create-gate's required-set derivation so a single derivation lives in the shared
  dis-validation lib, importable by both the streaming consumer and dis-ui-server.
- Replace the streaming consumer's hardcoded write-time required set with that derived set.
- The derivation is required-in-model intersected with mapping-produced, with enrichment-produced
  columns excluded from what the mapping must supply, so enrichment-guaranteed columns (currency)
  are not demanded of the mapping (D95 preserved).
- The derivation must be pinned to the hot model (the current-position model), independent of the
  routed target model, because the completeness question is always about the hot row.
- The consumer's "what this mapping guarantees" comparison must keep unioning enrichment-supplied
  columns, so currency remains satisfied.

The exact current location of the hardcoded set, the create-gate helper, and the mapping-produced
helper, and whether mapping_produced already lives in the shared lib or must move with the
derivation, are derived in plan mode from live code (cite path and line), not asserted from the
grounding.

## Scope

In scope:
- The shared derivation helper (its home in dis-validation).
- The streaming consumer's write-time completeness gate.
- dis-ui-server's create gate updated to import the promoted helper (no logic change; same
  derivation, now from the shared location).
- Tests proving identical verdicts.

Out of scope:
- The mandatory-flag / enrichment-asymmetry correction (currency mandatory=false) is Slice 16i.
- Any nullability change to canonical columns is Slice 16j.
- services/dis-ui is read-only, untouched.
- Any DDL, schema, role, or policy change (no migration in this slice).

## Open questions for plan mode

1. Live reconciliation. Confirm from live code (path and line) the current hardcoded write-time
   set, the create-gate derivation, the mapping-produced helper, and the enrichment-produced
   field set. Confirm the grounding's finding that the derived set and the hardcoded set produce
   identical COMPLETE/INCOMPLETE verdicts today; if they diverge for any case, surface it before
   proceeding.
2. Shared-home placement. Where in dis-validation the promoted derivation belongs, and whether
   mapping_produced is already shared or must move alongside it. Confirm no dependency inversion
   (the consumer and dis-ui-server may both import dis-validation; dis-validation must not import
   either service).
3. The hot-model pin. Confirm the derivation keys on the current-position (hot) model regardless
   of the routed target, and identify the exact place routing could wrongly substitute a
   different model (the one real trap), so a test pins against it.
4. Verdict-equivalence proof. The existing test surface that exercises COMPLETE/INCOMPLETE, and
   how to assert the derived set reproduces every prior verdict (the no-behavior-change
   guarantee), plus a mutation-evident test that the write gate's required set tracks the model
   derivation rather than a literal.

## Acceptance criteria

- The hardcoded write-time literal no longer exists; the write gate derives its required set from
  the canonical model via the shared helper.
- The derivation lives in dis-validation and is imported by both the streaming consumer and
  dis-ui-server, with no dependency inversion.
- The derived write-time set produces identical COMPLETE/INCOMPLETE verdicts to the prior
  hardcoded set for every existing case (proven; the no-behavior-change guarantee).
- The derivation is keyed to the hot model regardless of routed target; sales/change routing is
  unaffected, and a test pins this.
- Enrichment-supplied columns (currency, tax_treatment) remain satisfied via the enrichment
  union, not demanded of the mapping.
- The change is mutation-evident: a test pins that the write gate's required set matches the model
  derivation, so a future model nullability change is reflected with no gate code edit.
- Full suite green; make check, lint, mypy clean; tests ship in the same commit.
- services/dis-ui untouched.

## Constraints

- Libs are mechanism, not policy: the required set is derived from the canonical model, never
  re-baked as a literal.
- No DDL, no migration in this slice.
- No D-number assigned in the doc; the operator assigns it at commit.
- The grounding (docs/scratch/nullable-canonical-columns-grounding.md) is reference, not
  authority; plan mode reconciles it against live code.
