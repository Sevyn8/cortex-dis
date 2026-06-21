# Slice 5b: enrichment lib (libs/dis-enrichment) + streaming-consumer integration

A new pure shared lib, `libs/dis-enrichment`, a faithful sibling of `dis-mapping`, plus its
integration into the streaming consumer on the snapshot / current-position path only. The lib
owns a registered set of canonical fields and its output wins over the mapping; it resolves
each field from an authoritative internal source, handed in by the consumer (the lib does no
I/O). This doc is goal-level: the lib's interface, the consumer seam, the registry shape, the
completeness change, and the best implementation are CC's to design in plan mode and show for
review before any code. The decisions and constraints below are fixed. `services/dis-ui` is
Amit's and is never edited.

## Depends on

- Slice 5 (`dis-mapping`, `dis-validation`), built and pushed: the pure four-stage mapping
  engine (`apply_mapping`, frame-in/frame-out, no I/O, purity-contract enforced) and the
  Pandera pre/post validation suites. 5b mirrors `dis-mapping`'s lib shape and purity contract
  exactly, and slots alongside it in the pipeline-mechanics family.
- The streaming consumer's snapshot / current-position write path, built and pushed: the
  orchestration sequence (fetch, mapping load + routing, the internal-source read, pre/post
  validation, the catalogue/hot sink write), the `MappingResult` contract (mapping-produced
  columns only, with `source_row_indices`), and the existing `tax_treatment` store-injection.
- The existing store-existence precondition (the composite store FK and the `tax_treatment`
  store read that fails when the store is absent): 5b relies on this guarantee. A store not in
  the system cannot have data ingested, so the lib assumes the internal source row exists and
  its registered fields are populated.
- Decisions this slice honours: the mapping engine purity contract (no I/O in pure libs;
  services do the I/O and hand data in); the `MappingResult` row-alignment contract
  (`source_row_indices` parallel to rows); the mechanism-not-policy rule (business rules arrive
  as handed-in data, never baked as literals into lib code); `fresh == migrated` (no schema
  change in this slice, so not triggered, stated for completeness).

## Decisions to REGISTER (operator assigns numbers at the commit gate; D94 is next)

- **D94, enrichment runs before post-validation (Option A).** Enrichment is applied after the
  mapping engine produces its contribution and before the canonical-shape (post) validation,
  so enriched values are validated by the canonical-shape suite and participate in hot
  completeness. The long-term uniform invariant (every canonical value passes the same quality
  gate regardless of source) is chosen over the short-term simplicity of injecting at the
  current `tax_treatment` point after validation.
- **D95, enrichment output wins; registered fields become lib-guaranteed.** For a registered
  field, the lib's resolved value overrides any mapping-produced value. `currency` becomes
  lib-guaranteed and leaves the mapping's required-projection set (it is no longer required
  from the mapping); completeness counts enrichment-guaranteed fields. The lib's contract is
  source-agnostic: it resolves from an authoritative internal source. The internal source
  wired this slice is `identity_mirror.stores`, but the source is not part of the lib's
  identity (other internal sources may register later).
- **D96, missing-internal-source-row precondition (deferred).** Ratify and generalize the
  existing store-existence behaviour to all enrichment: ingestion fails when the internal
  source row is absent. Largely existing behaviour (the FK and `tax_treatment` read already
  enforce it); this entry generalizes it to the enrichment layer. Trigger: when enrichment
  extends beyond the store, or when the precondition is formalized in the lib's contract.
- **D97, field-blank-on-an-existing-source loud-fail guard (deferred).** If a registered
  enrichment field is unexpectedly blank on an existing internal source row, fail loud and
  early rather than write a silent NULL. New guard. This slice relies on the current
  NOT-NULL-on-source reality (the registered store fields are NOT NULL); D97 removes that
  assumption. Trigger: a registered field whose source column is nullable, or a hardening pass.
- **D98, tax_treatment migrates to the lib on the current-position path.** Under D94,
  `tax_treatment` moves from its hardcoded injection into the enrichment lib on the
  current-position path and becomes validated (a deliberate behaviour change from today's
  unvalidated injection). The plan states whether the event-path `tax_treatment` hardcode
  remains (event paths are out of this slice) and, if so, records the temporary duplication
  (lib on current-position, hardcode on event paths) with its removal trigger: when enrichment
  extends to the event tables.

## Goal

After this slice, the streaming consumer enriches the snapshot / current-position write path
through a new pure lib, `libs/dis-enrichment`. For each registered canonical field, the lib's
value (resolved from an authoritative internal source the consumer reads and hands in) wins
over the mapping. The first registered fields are `currency` and `tax_treatment`. Enrichment
runs before post-validation, so enriched values are validated and count toward completeness.
The change is backend-only: no user-facing, mapping, or UI change (a user still maps
`currency` as today; the internal-source value silently wins at processing). The lib is a
faithful sibling of `dis-mapping`: pure, frame-in/frame-out, no I/O, with the same scaffold and
purity contract; the consumer does the internal-source read and hands the facts to the lib.

### What the lib is

- A pure shared lib (no I/O of any kind at runtime, enforced by the same import-linter
  forbidden-modules contract and purity test as `dis-mapping`). It receives the mapping output
  and the already-read internal-source facts, and returns the enriched output. It never reads
  a database.
- It owns a registry of canonical fields. The registry is a code-owned declaration behind a
  swappable seam (a config table can replace the source later without changing the lib's
  resolution logic). Each entry carries at least: the canonical field, the internal source and
  field it resolves from, and a per-field table-scope flag (which canonical tables the field
  enriches). Only the current-position table is wired this slice; the flag exists in the shape.
- A single resolution strategy this slice: an unconditional internal-source lookup (the lib's
  value always wins). The contract admits a future conditional strategy without redesign; no
  strategy framework is built now.
- Lookup-enrichment only. The lib never computes or derives attributes from accumulated data
  (velocity, stock age, cost trend); those are the separate `daily-compute` service and are
  out of scope, permanently, for this lib.

### What the consumer does

- Reads the internal source once for the registered fields (widening the existing
  `tax_treatment` store read to also return `currency`, same key, single round-trip) and hands
  the facts to the lib.
- Applies enrichment on the snapshot / current-position path only, gated so the event path is
  untouched (the seam sits upstream of the snapshot/event branch; its effect must be confined
  to the current-position target).
- Places enrichment before post-validation (D94), so enriched values are validated and counted
  in completeness.
- Mirrors the `dis-mapping` integration pattern: a service wrapper around the lib (as
  `apply_loaded_mapping` wraps `apply_mapping`), the lib called with handed-in facts.

## Principles the plan must honor (CC proposes how; these are constraints, not solutions)

- **Pure lib, consumer does the I/O.** The lib receives already-read facts and returns output;
  it never reads a database. The import-linter forbidden-modules contract and the purity test
  enforce this, copied from `dis-mapping` and adapted. The internal-source read stays
  consumer-side.
- **Source-agnostic lib identity.** The lib resolves from an authoritative internal source.
  `identity_mirror.stores` is the first source, not the lib's definition. Naming, the registry
  shape, and the interface must not bake "store" into the lib's identity.
- **Output wins, with explicit override ordering.** A registered field's enriched value must
  override any mapping-produced value for that field. The plan shows how (set after the
  mapping output, or strip the field from the mapping output), so a same-named mapping value
  cannot win by accident. `currency` is a mapping-produced column today, so this collision is
  real and must be handled.
- **Row alignment is a hard contract.** Enrichment is column-wise mutation, never row
  filtering. The output preserves row count and `source_row_indices` ordering, or the
  downstream alignment breaks.
- **Completeness counts enrichment-guaranteed fields (D94, D95).** `currency` leaves the
  mapping's required-projection set and becomes lib-guaranteed; completeness classification
  must count enrichment-guaranteed fields so the snapshot complete-path classification stays
  correct. This touches the load-bearing completeness lever and must be deliberate and tested.
- **Snapshot path only; event path untouched.** The seam is upstream of the snapshot/event
  branch, so its effect must be gated to the current-position target. The event path (sale,
  change) is out of scope and must not change behaviour, including its existing `tax_treatment`
  injection.
- **No user-facing or mapping change.** The user still maps `currency` as today; the
  internal-source value wins silently at processing. No UI, no template-builder, no mapping
  vocabulary change this slice. The create-time "these fields are internally-sourced" note is a
  deferred build-plan follow-up, not built here.
- **Lib mirrors the repo's lib conventions.** The lib follows the established shared-lib
  structure (own folder, `CLAUDE.md`, `README.md`, `pyproject.toml`, `src` package with
  `py.typed`, rootless `tests/unit`), the uv-workspace wiring, the import-linter purity
  contract, the mypy package list, and the isort first-party entry, all mirroring `dis-mapping`.

## Scope

In: the new `libs/dis-enrichment` lib (scaffold, registry, resolution interface, purity
contract); the consumer integration on the snapshot / current-position path (the service
wrapper, the widened internal-source read, the seam before post-validation gated to
current-position, the output-wins ordering); `currency` and `tax_treatment` as the registered
fields; the `tax_treatment` migration to the lib on the current-position path (D98); the
completeness and required-projection change for `currency` (D94, D95); the workspace,
packaging, purity, and mypy wiring; tests in the same commit.

Out (with where each lands):
- Event / history paths (sale, change): enrichment does not touch them this slice. The
  event-path `tax_treatment` hardcode stays; D98 records any resulting temporary duplication
  and its removal trigger.
- Other internal sources beyond `identity_mirror.stores`: the registry shape admits them; none
  wired now.
- Derived / computed attributes (velocity, stock age, cost trend): the `daily-compute` service,
  never this lib.
- A conditional resolution strategy: the contract admits it; not built now.
- The create-time "these mapped fields are internally-sourced" response note: deferred,
  build-plan TODO (the create endpoint reads the registry to generate it; out of this slice).
- D96 (missing-source-row precondition) and D97 (field-blank-on-source loud-fail): recorded as
  decisions, not built this slice.
- Any edit to `services/dis-ui` (Amit's; READ-ONLY).

## Acceptance criteria

Each criterion names the test that proves it and what that test must assert. Per the standing
loop rules: a test's expected value comes from a source independent of the implementation (a
value hand-derived from the rule, not copied from the code, or a plausible-but-wrong result
passes); load-bearing proofs ERROR at setup, never skip and never fall back to a guessed
default (a test that skips when its dependency is absent reports green having run nothing);
tests land in the same commit as the code. Properties a test cannot prove (the absence of a
behaviour) are marked review-only and routed to a scope or import check, not asserted by a
passing test.

1. **The lib is pure.** `libs/dis-enrichment` exists, mirroring `dis-mapping`'s structure. The
   import-linter forbidden-modules contract names `dis_enrichment` and forbids the I/O-bearing
   modules (`sqlalchemy`, `psycopg`, `google`, the I/O libs), and `lint-imports` is green. The
   purity test (a fresh-interpreter subprocess probe, copied from `dis-mapping`'s and adapted)
   asserts none of the forbidden runtime client modules land in `sys.modules` after importing
   the lib; it ERRORS, never skips, if the probe cannot run. Review-only: that the lib's public
   interface receives handed-in facts and returns output and never opens a DB handle is a
   scope/signature property (a test cannot prove a DB read's absence); confirmed by the import
   contract plus a signature review, recorded as review-only.

2. **Enrichment output wins over the mapping.** A test seeds a mapping that produces a
   `currency` value and an internal-source fact carrying a different `currency`, runs the
   enrichment, and asserts the enriched output carries the internal-source value, not the
   mapping's. The expected value is the seeded source fact (independent of the resolution
   code). The same test (or a sibling) proves the override holds for a field that is a
   mapping-produced column today (`currency` is), so the collision path is exercised, not just
   the no-collision path.

3. **Enriched values are validated (D94).** A test routes a chunk whose enriched `currency`
   would FAIL the canonical-shape suite and asserts the post-validation gate rejects it,
   proving enrichment runs before post-validation and the enriched value is seen by the suite.
   A second test routes a valid enriched value and asserts it passes. (If a valid internal
   source can never produce an invalid `currency` given the NOT-NULL char(3) reality, the test
   induces the failure with a deliberately invalid handed-in fact, so the ordering property is
   proven independent of whether the live source can produce it.)

4. **Completeness counts enrichment-guaranteed fields (D94, D95).** A test asserts a snapshot
   mapping that does NOT supply `currency` (because the lib now guarantees it) classifies as
   hot-complete, where before the change it would not. The expected classification is derived
   from the rule (`currency` is lib-guaranteed, so the required-projection set no longer
   includes it), not read from the code. A companion test asserts a mapping still missing a
   genuinely-required projected field is still classified incomplete, so the change narrowed
   the required set by exactly `currency` and nothing else.

5. **Row alignment is preserved.** A test runs enrichment over a multi-row contribution and
   asserts the output has the same row count and the same `source_row_indices` ordering as the
   input, and that the downstream catalogue assembly zip aligns. The test uses a contribution
   of more than one row with distinguishable values, so a reorder or row-count change would be
   caught.

6. **`tax_treatment` migrates and is now validated (D98).** A test asserts `tax_treatment` on
   the current-position path is produced by the lib (not the old hardcoded injection) and that
   it now passes through post-validation (the behaviour change from today's unvalidated
   injection). If the event-path `tax_treatment` hardcode remains, a test or scope check
   confirms it is untouched and D98 records the duplication and removal trigger.

7. **The event path is unchanged.** A test exercises the sale (and change) path and asserts its
   canonical output is identical to pre-slice behaviour, including its existing `tax_treatment`
   injection. Review-only: that the enrichment seam does not affect the event path is a scope
   property (the seam sits upstream of the snapshot/event branch and must be gated to the
   current-position target); confirmed by the gating test above plus a scope/import check that
   the enrichment effect is reachable only on the current-position branch, recorded as
   review-only.

8. **No user-facing, mapping, or UI change.** `services/dis-ui` is unmodified (a scope check
   over the diff confirms no file under it changed). No mapping-vocabulary or
   template-validation change: a test or scope check confirms a user mapping `currency` still
   succeeds at create exactly as today (the value is silently overridden at processing, not
   rejected at create).

9. **Register and plan hygiene.** Decisions D94, D95 registered; D96, D97 registered as
   deferred with their triggers; D98 registered with the event-path status and any
   duplication's removal trigger. The build-plan create-time-note TODO recorded. The
   build-guide Slice 5b line flips TODO to DONE (status word only, in place).

10. **Gates green.** `make check` shows no tier regression and the new tests pass; `mypy
    --strict` clean under the per-package gate (with `libs/dis-enrichment` added to the package
    list in the same commit that clears it); `ruff` and `lint-imports` clean; the full test
    suite green; tests in the same commit as the code.
