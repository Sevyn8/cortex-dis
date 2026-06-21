# Slice 27: Sources CRUD (the last tenant screen)

**Status:** TODO.

**Phase:** tenant. The final DIS UI screen. Builds on slices 20 to 26.

**Owners:** UI track owns the screen and defines the source create/edit/deprecate API shapes; Sanjeev owns the source record schema and where source registration lives.

## Goal

Add create, edit, and deprecate for tenant sources, completing the Sources surface (the read-only index from slice 20 gains full CRUD). Create and edit are routed pages (/sources/new and /sources/:sourceId/edit); delete is deprecate-only (a soft status transition, never a hard delete). Built in the slice 23 design system at the craft bar, light and dark.

This is the last DIS UI screen. After it, the DIS UI is feature-complete on fixtures.

## The source model and the honesty about it

A source is the composite (tenant_id, source_id), where source_id is a free-form kind-style string (manual_csv_upload, shopify_pos_v2). There is no config.sources registry table and no allocation rule by design; tenant_id comes from auth and source_id is the kind string. The Sources index also shows name, type, and store, which are UI display fields with no confirmed schema home.

The operator chose the fuller form: the create/edit form treats name, type, and store as real source-record fields. This is a legitimate choice under the division of authority (the UI defines the API shape it wants, including these fields, and Sanjeev builds the backend to it), but it means this slice DEFINES more of the source record than the schema currently confirms. So the honesty requirement: the entire create/edit request shape (including name, type, store) is a UI-DEFINED PROVISIONAL contract, contained in one place and flagged, and the source record schema (whether name/type/store are real columns or UI metadata) plus where source registration lives are recorded as Sanjeev's to confirm. We are not asserting the schema; we are proposing the API shape the UI needs.

## Two create paths, reconciled

The onboarding Sample Upload screen already creates a source via its "Attach to: new source" flow (uploading a sample to a brand-new source declares that source). This slice adds an explicit declarative create. These are two paths to the same outcome and MUST define a source the same way: the CRUD create request shape and the onboarding attach-to-new source fields must be consistent (same field names, same source_id derivation, same provisional shape). The slice reconciles against the onboarding fixture so they agree; it does not introduce a second, divergent source shape.

## Scope

In:
- Create: a /sources/new page with the source form (the fuller form: source_id/kind, name, type, store, reconciled with onboarding attach-to-new)
- Edit: a /sources/:sourceId/edit page editing the source's display metadata (not its identity; source_id is the key and is not re-keyed on edit)
- Deprecate: a soft status transition (active to deprecated) with a confirm, from the index row and/or the edit page; NO hard delete
- the Sources index (slice 20) gains a Create action and per-row Edit/Deprecate actions
- all create/edit/deprecate shapes contained as flagged provisional constants, reconciled with onboarding

Out (flag as seams):
- hard delete (never; sources have canonical rows and audit history behind them)
- re-keying a source_id (identity is immutable once created)
- bulk operations
- any backend source-registration implementation (the UI defines the shape; Sanjeev builds it)

## Hard constraints

1. **Deprecate-only, never hard delete (FM1).** "Delete" is a soft status transition to deprecated, with a confirm. A source with canonical/audit data behind it must never be hard-deleted from the UI. No destructive delete path exists in this slice.
2. **Identity is immutable.** source_id is the key; edit changes display metadata, never the source_id. Create sets it once.
3. **One create shape, reconciled with onboarding (FM2).** The CRUD create request and the onboarding attach-to-new source fields are the SAME shape (field names, source_id derivation). Reconcile against the onboarding fixture; do not introduce a divergent source shape.
4. **Containment.** The create request, edit request, deprecate request, and any new source-record fields are flagged provisional constants in the source fixture layer (extend the existing sources/onboarding fixture or a clearly flagged section), naming the relevant demand list sections. Single reconciliation point. The source record schema is recorded as Sanjeev's to confirm.
5. **Tenant-scoped.** These are tenant operations (a tenant manages its own sources). Not ops, not cross-tenant. Standard tenant auth; no new auth logic.
6. **Design system at the craft bar.** The forms (labeled fields, selects, the create/edit pages), the index actions, and the deprecate confirm (Dialog) use the slice 23 primitives. Light and dark.
7. **No backend, no contract edits, no canonical changes.** Pure UI on fixtures (mutable-fixture pattern, as used for notifications/quarantine/shadow). Tokens unchanged.
8. **Repo hygiene + git discipline.** No em-dashes; precise DIS / DIS UI / dis-ui-server; strict TS no any; ESLint/Prettier clean. Claude Code does not push; operator reviews and pushes; no Co-Authored-By; one coherent commit.

## Acceptance criteria

1. /sources/new renders the source create form at the craft bar (source_id/kind, name, type, store), with validation (required source_id, etc.).
2. Submitting create adds the source to the fixture (mutable-fixture pattern) and the Sources index reflects it; the create shape matches the onboarding attach-to-new shape.
3. /sources/:sourceId/edit renders the edit form prefilled; editing updates the display metadata in the fixture; source_id is shown read-only (identity immutable) and is not re-keyed.
4. Deprecate is a soft transition (active to deprecated) with a confirm dialog; the index reflects the deprecated status as a Badge; there is NO hard-delete control anywhere.
5. The Sources index gains a Create action and per-row Edit and Deprecate actions, at the craft bar.
6. All create/edit/deprecate shapes are flagged provisional constants in the fixture layer, reconciled with onboarding (one source shape); the source record schema is recorded as Sanjeev's open item.
7. Tenant-scoped; the existing tenant auth applies; no ops/cross-tenant surface.
8. Craft-bar look light + dark; shared state components; forms use labeled fields and the Dialog primitive for the deprecate confirm.
9. pnpm install, dev (200, no console errors), build, test, lint, tsc --noEmit strict all green; the full prior suite stays green (the Sources index gains actions; onboarding reconciled, its existing tests intact or selector-only).
10. No em-dashes; correct naming; grounded ids (external strings, D37 open); tokens unchanged.

## Failure modes

- **FM1: A hard-delete path.** There is none. Delete is deprecate (soft) only. If a hard delete seems implied, it is not in scope; deprecate is the only destructive-looking action and it is reversible-in-principle (a status).
- **FM2: Divergent source shapes.** The CRUD create and the onboarding attach-to-new must define a source identically. If reconciling forces a change to onboarding, keep onboarding's behavioral tests green (selector-only changes), and do not fork the shape.
- **FM3: Asserting the source schema.** The UI defines the create/edit API shape it wants; it does NOT assert that name/type/store are real columns. Flag the source record schema and source registration home as Sanjeev's to confirm. Surface, do not invent.
- **FM4: Re-keying identity.** Edit must not change source_id. It is the key; show it read-only on edit.
- **FM5: Scope creep.** No hard delete, no bulk ops, no re-keying, no backend registration this slice. Note seams.

## Plan-mode prompt (single checkpoint)

> "Read docs/skills/sevyn8-workflow/SKILL.md, this slice doc, services/dis-ui/docs/dis-ui-visual-craft-spec.md, services/dis-ui/docs/dis-ui-surface-map.md (Sources and the onboarding flow), docs/ui-engineer-demand-list.md (the sources/onboarding sections), services/dis-ui/CLAUDE.md, and your current code: the Sources index (SourcesIndex.tsx + its fixture + tests), the onboarding Sample Upload attach-to-new flow (SampleUpload.tsx + onboarding.ts + tests) to reconcile the create shape, the mutable-fixture pattern (notifications/quarantine/shadow), src/auth, the route table, and the slice 23 primitives (forms, Dialog, Button, Select, Badge).
>
> Produce a plan to:
> 1. define the source create/edit/deprecate request shapes as flagged provisional constants in the source fixture layer, RECONCILED with the onboarding attach-to-new source fields (one shape, same field names and source_id derivation); record the source record schema + source registration home as Sanjeev's open item
> 2. build /sources/new: the create form at the craft bar (source_id/kind, name, type, store) with validation; submit adds to the mutable fixture; the index reflects it
> 3. build /sources/:sourceId/edit: prefilled edit of display metadata; source_id read-only (identity immutable, FM4); submit updates the fixture
> 4. add deprecate: a soft active-to-deprecated transition with a Dialog confirm, from the index row (and/or edit page); status shows as a Badge; NO hard delete (FM1)
> 5. the Sources index gains a Create action and per-row Edit/Deprecate actions at the craft bar
> 6. tests: create adds a source and the index reflects it; the create shape matches onboarding's; edit updates metadata and source_id is read-only; deprecate transitions status with a confirm and no hard-delete control exists; index actions render; onboarding tests stay green (selector-only if touched)
>
> Return the file list, the reconciled source shape and where it lives, the create/edit/deprecate approach, the index action additions, and the test list (explicitly: confirm onboarding behavioral tests are unchanged or selector-only, and that no hard-delete path exists). FM2: one source shape, reconciled with onboarding. FM3: flag the source schema as Sanjeev's, do not assert it. FM1/FM4: deprecate-only, source_id immutable. Return the plan and STOP."

## After approval

Execute, then verify: install / dev / build / test / lint / tsc strict all green with the count; an acceptance-criteria table for criteria 1 to 10; explicitly confirm (a) no hard-delete path exists (deprecate only), (b) the create shape matches onboarding's attach-to-new, (c) source_id is read-only on edit, (d) onboarding tests unchanged or selector-only; craft-bar look light + dark; no em-dashes, correct naming, tokens unchanged. One commit, subject "services/dis-ui: Slice 27 Sources CRUD (create, edit, deprecate)", no Co-Authored-By. Do not push; show the diff summary and hash and stop.

## Carry-forward

- With this slice the DIS UI is feature-complete on fixtures: all tenant screens and the full ops cluster, on the design system at the craft bar, light and dark.
- The batched Sanjeev message now has its full reconciliation list: the UI-defined API shapes he builds (onboarding, mapping CRUD, quarantine resubmit, shadow, fleet, notifications link, and now source create/edit/deprecate), and the genuine policy/schema questions only he answers (RBAC vocabulary D25, GET /me profile call, source record schema + registration home, cross-tenant ops read, cross-tenant ops resubmit authorization, the DuckDB query engine + safety model + cross-tenant SQL authorization, the id-space D37). Plus the demand-list illustrative-id sweep.
- After the message and his answers, the next phase is the real-mode switch when slice 13 lands dis-ui-server (flipping every fixture to real calls, reconciling each contained provisional shape against his actual contracts).
