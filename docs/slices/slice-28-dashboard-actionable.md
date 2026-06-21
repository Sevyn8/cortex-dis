# Slice 28: Make the Tenant Dashboard actionable

**Status:** TODO.

**Phase:** tenant. A small UX completeness slice. Builds on slices 20 to 27.

**Owners:** UI track. No backend or Sanjeev dependency; this is pure UI navigation on existing screens.

## Goal

The Tenant Dashboard is currently a read-only dead-end: the "Health by source" rows and the open-quarantine counts display data but link nowhere, so a tenant who sees a problem has no path from the Dashboard to acting on it. This slice makes the Dashboard a jumping-off point:
- a source row links to that source's mappings
- the open-quarantine count (when greater than zero) links to the Quarantine console pre-filtered to that source

To support the second, the Quarantine console gains the ability to read a ?source= query param and pre-apply its existing source filter. This also closes a previously-flagged gap (the demand list referenced a /quarantine?source= link target the console did not consume).

Pure UI on fixtures, at the slice 23 craft bar. No new data, no contract change.

## The matching constraint (the thing that can silently break)

The Dashboard rows and the Quarantine filter must agree on the key. The ?source= value the Dashboard puts in the link, and the value the Quarantine filter matches on, must both be the source_id (the composite key, e.g. manual_csv_upload), NOT the display name (e.g. "Manual CSV Upload"). If the link carries a display name but the filter keys on source_id, the prefilter silently matches nothing, the page loads but shows everything (or nothing) instead of the filtered view.

So: link by source_id, filter by source_id, and the test must assert the filter is APPLIED (the list is narrowed), not merely that navigation occurred.

## Scope

In:
- Dashboard: each "Health by source" row's source links to that source's mappings (the existing mappings route for that source_id)
- Dashboard: the open-quarantine count, when greater than zero, links to /quarantine?source=<source_id>; when zero, it is not a link (nothing to see)
- Quarantine console: read a ?source= query param on mount and pre-apply the existing source filter to it; with no param, behave exactly as today
- the link param and the filter key are both source_id

Out:
- linking the latency metric cards (no sensible drill-down target)
- per-health-state actions beyond linking the row
- any new filter capability on Quarantine beyond consuming the existing source filter from the URL
- the ops cross-tenant Quarantine mode (this is the tenant Dashboard and tenant Quarantine)

## Hard constraints

1. **Match on source_id, not display name (FM1).** Both the Dashboard link param and the Quarantine filter use source_id. The prefilter must actually narrow the list. A test asserts the narrowed result, not just navigation.
2. **Quarantine change is additive (FM2).** Reading ?source= and pre-applying the existing filter is additive. The existing Quarantine filter behavior and all its tests stay green with assertions intact. With no ?source= param, the console behaves exactly as today. A new test covers the pre-filtered arrival.
3. **Dashboard changes are pure addition.** Wrapping the source name and the quarantine count in links does not change what the Dashboard displays or computes; only adds navigation.
4. **Tenant-scoped.** This is the tenant Dashboard and the tenant Quarantine mode. Does not touch the ops cross-tenant Quarantine mode (isOps branch) or any ops surface.
5. **Craft bar.** Links use the design-system link/button styling; the table stays at the craft bar. No raw anchors styled ad hoc.
6. **No backend, no contract edits, no canonical/token changes.** Pure UI on fixtures.
7. **Repo hygiene + git discipline.** No em-dashes; precise DIS / DIS UI / dis-ui-server; strict TS no any; ESLint/Prettier clean. Claude Code does not push; operator reviews and pushes; no Co-Authored-By; one coherent commit.

## Acceptance criteria

1. On the Dashboard, each source row's source name is a link to that source's mappings (by source_id).
2. On the Dashboard, a source with open quarantine greater than zero shows the count as a link to /quarantine?source=<source_id>; a source with zero open quarantine shows the count as plain text (no link).
3. The Quarantine console, arriving with ?source=<source_id>, pre-applies its source filter to that source and the list is narrowed accordingly.
4. The Quarantine console with no ?source= param behaves exactly as before (existing filter behavior and tests intact).
5. The link param and the Quarantine filter key are both source_id (the prefilter actually matches; a test asserts the narrowed list).
6. Links use the craft-bar styling; the Dashboard otherwise unchanged (same data, same layout).
7. Tenant-scoped; the ops cross-tenant Quarantine mode is untouched.
8. pnpm install, dev (200, no console errors), build, test, lint, tsc --noEmit strict all green; the full prior suite green (Quarantine existing tests intact, new prefilter test additive, Dashboard tests intact or selector-only for the new links).
9. No em-dashes; correct naming; grounded ids (source_id strings); tokens unchanged.

## Failure modes

- **FM1: Display-name/source_id mismatch.** The link and the filter must both use source_id. If they disagree, the prefilter silently fails. The test asserts the narrowed list, catching this.
- **FM2: Quarantine regression.** Reading ?source= is additive; the existing filter behavior and tests stay green with assertions intact. With no param, no behavior change. If an existing Quarantine assertion would change, STOP and surface.
- **FM3: Touching the ops mode.** The Quarantine ?source= reading applies to the tenant mode; do not alter the ops cross-tenant mode's behavior.
- **FM4: Scope creep.** No latency-card links, no new filters, no per-state actions. Just the two link targets and the param read.

## Plan-mode prompt (single checkpoint)

> "Read docs/skills/sevyn8-workflow/SKILL.md, this slice doc, services/dis-ui/docs/dis-ui-visual-craft-spec.md, services/dis-ui/CLAUDE.md, and your current code: the Tenant Dashboard (Dashboard.tsx + its fixture + tests), the Quarantine console (QuarantineConsole.tsx + quarantine.ts + tests, especially the existing source filter and the isOps branch), the Sources mappings route (how a source links to its mappings, by source_id), and the route table.
>
> Produce a plan to:
> 1. Dashboard: make each Health-by-source row's source name a link to that source's mappings (by source_id); make the open-quarantine count a link to /quarantine?source=<source_id> when greater than zero, plain text when zero. Use the craft-bar link styling. The Dashboard's data and layout otherwise unchanged.
> 2. Quarantine console: on mount, read a ?source= query param and pre-apply the EXISTING source filter to it (tenant mode); with no param, behave exactly as today. Match on source_id. Do NOT change the ops cross-tenant mode.
> 3. tests: Dashboard renders the source link (to mappings by source_id) and the quarantine-count link (to /quarantine?source=<source_id>) when greater than zero, plain text when zero; Quarantine arriving with ?source=X narrows the list to X (assert the narrowed result, not just navigation); Quarantine with no param behaves as before (existing tests intact); the ops mode is untouched.
>
> Return the file list, the link targets (confirm source_id, not display name), the Quarantine param-read approach, and the test list (explicitly: which existing tests change and whether selector-only; the existing Quarantine filter assertions must NOT change). FM1: match on source_id; the test asserts the narrowed list. FM2: Quarantine param-read additive. FM3: ops mode untouched. Return the plan and STOP."

## After approval

Execute, then verify: install / dev / build / test / lint / tsc strict all green with the count; an acceptance-criteria table for criteria 1 to 9; explicitly confirm (a) the link and filter both use source_id and the prefilter narrows the list (name the test), (b) Quarantine with no param is unchanged, (c) the ops cross-tenant mode is untouched; craft-bar look; no em-dashes, correct naming, tokens unchanged. One commit, subject "services/dis-ui: Slice 28 make the Tenant Dashboard actionable", no Co-Authored-By. Do not push; show the diff summary and hash and stop.

## Carry-forward

- Remaining catalogued findings (not this slice): the sample-upload-doesn't-parse confusion (a demo-data banner, optional), and the open questions for the batched Sanjeev message (source record schema, source registration, store semantics, FTP/API ingestion scope, the underdefined Mapping Review sample rendering, the missing live-ingestion upload screen, plus D25/GET-me/cross-tenant policies/DuckDB engine/D37).
- The batched Sanjeev message remains the main next step after these small UX fixes.
