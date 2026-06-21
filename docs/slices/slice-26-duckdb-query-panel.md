# Slice 26: DuckDB Query Panel (ops SQL over the bronze blob)

**Status:** TODO.

**Phase:** Phase 3 (ops cluster). The last and most speculative ops screen. Builds on slices 20 to 25.

**Owners:** UI track owns the screen and the query API shapes it defines; Sanjeev owns the query engine, the execution contract, and the entire safety/access model.

## Goal

Build the ops-only DuckDB Query Panel (demand list 7.4 to 7.5): a free-form SQL editor over the bronze blob (raw landed data) with a dynamic result grid, for ops users. Reached at /ops/query under the OpsBoundary guard.

This slice builds the OPERATOR EXPERIENCE on a contained fixture with execution STUBBED. It does not execute real SQL and does not invent the execution engine, the result contract, or the safety model. The UI defines the request, result, and error shapes it wants and renders a convincing fixture; the engine and its guardrails are Sanjeev's, recorded as the heaviest open contract in the UI.

Built in the slice 23 design system at the craft bar, light and dark.

## Why this is the most speculative screen (named up front)

Free-form SQL over the bronze blob, cross-tenant, read by an ops user, is the single highest-blast-radius capability in the DIS UI. 7.4 to 7.5 is thin, and almost everything about how it really works is undefined and is Sanjeev's to define:
- the execution contract (the query API request/response, how a query is submitted and results returned, streaming vs batch)
- the safety model: read-only enforcement, row caps, statement timeouts, resource limits, query sandboxing, what SQL is even permitted
- the access model: this slice assumes cross-tenant by default (ops queries across the bronze blob), but whether ops truly gets unrestricted cross-tenant SQL over raw data, and how that is authorized and isolated, is Sanjeev's RLS/security policy

So this slice is honest about being a shell: real SQL is not executed, the result is a fixture, and every one of the above is a flagged open item, not a UI invention. The UI's job is the operator experience and the API shape it needs; the engine and its guardrails are flagged for Sanjeev.

## Scope

In:
- a SQL panel screen at /ops/query under OpsBoundary (ops-only; non-ops denied, inherits slice 24)
- a SQL editor using CodeMirror 6 with SQL highlighting (@codemirror/lang-sql)
- a Run action that calls a STUBBED, contained query function (no real execution)
- a dynamic-columns result grid: renders whatever columns/rows the (fixture) result returns; this is the one screen with no fixed result shape
- the four states: idle (no query run yet), running, result (the grid), error (a SQL error message), plus empty-result
- a contained fixture mapping a few canned query strings to canned dynamic result sets, cross-tenant
- the ops nav group gains a Query entry (ops-only)

Out (flag as seams, do not build):
- real SQL execution / a real query engine
- the safety model (read-only, row caps, timeouts, sandboxing)
- query history, saved queries, export/download of results
- pagination of large result sets
- any tenant-facing exposure (ops only)

## Dependency de-risk (do first)

CodeMirror 6 is the first runtime dependency added outside the design system. Before building the screen on it, prove it mounts and behaves under Vite 8 + React 19 (the same gate discipline used for Base UI in slice 23): a CodeMirror editor with the SQL language extension mounts, renders, accepts input, and its value is readable. If it does not mount cleanly, STOP and report (fallback: a styled textarea), do not build the screen on an unproven editor.

## Hard constraints

1. **Execution is stubbed; the engine is not invented (FM1).** The Run action calls a contained stub that returns fixture results. The UI does NOT implement, simulate the internals of, or claim a real query engine. The query API shape (request/result/error) is the UI's definition; the engine is Sanjeev's.
2. **The safety and access model is Sanjeev's (FM2).** Read-only enforcement, row caps, timeouts, sandboxing, and cross-tenant authorization are flagged open, not invented. The UI may show affordances that imply them (for example a row-count note) but does not enforce or define them.
3. **Containment.** The query request shape, the dynamic result shape, the error shape, and the canned query-to-result fixture are flagged provisional constants in src/lib/dis-ui-server/ops-query.ts, naming demand list 7.4 to 7.5. Single reconciliation point.
4. **Cross-tenant fixture by default.** The fixture results span tenants (a tenant column where relevant). The cross-tenant access policy is recorded open (shared with slices 24 and 25).
5. **Ops route under the guard.** /ops/query sits under OpsBoundary; non-ops denied. No new auth logic; isOps is the gate.
6. **Design system at the craft bar.** The editor, Run button, result grid, and states use the slice 23 primitives and tokens. The CodeMirror theme is tokenized to match (light and dark) rather than its default theme, as far as is reasonable.
7. **Scoped dependency only.** Add only CodeMirror 6 and its SQL language extension (codemirror, @codemirror/lang-sql, @codemirror/state, @codemirror/view, or the umbrella codemirror package). No other deps.
8. **No backend, no contract edits, no canonical changes.** Pure UI on fixtures. Tokens unchanged.
9. **Repo hygiene + git discipline.** No em-dashes; precise DIS / DIS UI / dis-ui-server; strict TS no any; ESLint/Prettier clean. Claude Code does not push; operator reviews and pushes; no Co-Authored-By; one coherent commit.

## Acceptance criteria

1. CodeMirror mounts and behaves under Vite + React 19 (the de-risk gate passes); if it had failed, the slice would have stopped.
2. /ops/query renders for ops: a SQL editor (CodeMirror, SQL highlighting) and a Run action.
3. Running a (fixture-recognized) query renders a dynamic-columns result grid matching the returned columns/rows; the grid handles arbitrary column sets.
4. The states work: idle before any run, running on submit, result on success, error (with a SQL-style message) for a fixture error query, empty-result for a zero-row result.
5. A non-ops persona is denied on /ops/query (inherits OpsBoundary).
6. The ops nav group shows a Query entry for ops, hidden for tenant.
7. The query request/result/error shapes and the canned fixture are flagged provisional constants in ops-query.ts; the screen consumes typed values only; execution is stubbed.
8. Craft-bar look in light and dark, including the editor theme; shared state components where applicable.
9. pnpm install, dev (200, no console errors), build, test, lint, tsc --noEmit strict all green; the full prior suite stays green (additive; no other screen changes).
10. No em-dashes; correct naming; grounded ids; tokens unchanged.

## Failure modes

- **FM1: Pretending to execute.** The engine is stubbed and flagged. Do NOT implement a real query path, do NOT simulate engine internals, do NOT imply the result is anything but a fixture. The UI defines the API shape; the engine is Sanjeev's.
- **FM2: Inventing the safety model.** Read-only, row caps, timeouts, sandboxing, cross-tenant authorization are flagged open. Do not invent or enforce them in the UI.
- **FM3: Shape leakage.** Query shapes and the fixture stay in ops-query.ts, not threaded into the screen. Single reconciliation point.
- **FM4: Dependency surprise.** CodeMirror is proven to mount first (the de-risk gate). If it does not, fall back to a styled textarea and report, rather than building on an unproven editor.
- **FM5: Scope creep.** No query history, saved queries, export, pagination, or real execution this slice. Note seams.

## Plan-mode prompt (single checkpoint)

> "Read docs/skills/sevyn8-workflow/SKILL.md, this slice doc, services/dis-ui/docs/dis-ui-visual-craft-spec.md, services/dis-ui/docs/dis-ui-surface-map.md (the DuckDB Query Panel and the ops cluster), docs/ui-engineer-demand-list.md 7.4 to 7.5, services/dis-ui/CLAUDE.md, and your current code: the OpsBoundary guard + ops routing/nav from slices 24 to 25 (AppRoutes.tsx, nav.ts), src/auth (isOps), the slice 23 primitives and the shared state components, and package.json for the dependency add.
>
> Produce a plan to:
> 1. DE-RISK FIRST: a minimal CodeMirror 6 + @codemirror/lang-sql mount check under Vite + React 19 (mounts, renders, accepts input, value readable). If it cannot mount cleanly, STOP and report (fallback: styled textarea); do not build the screen on an unproven editor.
> 2. add src/lib/dis-ui-server/ops-query.ts with the query request shape, the DYNAMIC result shape (columns + rows, arbitrary), the error shape, and a stubbed execute function mapping a few canned cross-tenant query strings to canned results (including an error query and an empty-result query), all flagged provisional naming 7.4 to 7.5
> 3. build the SQL panel at /ops/query at the craft bar: the CodeMirror SQL editor (tokenized theme for light/dark), a Run button (primary), a dynamic-columns result grid that renders whatever columns/rows come back, and the idle/running/result/error/empty states
> 4. register /ops/query under OpsBoundary; add the ops nav Query entry (ops-only)
> 5. tests: editor renders and accepts input; Run on a canned query renders the dynamic grid with the right columns; an error query shows the error state; an empty-result query shows empty; non-ops denied on /ops/query; nav visibility; query shapes are flagged constants, execution stubbed
>
> Return the file list, the CodeMirror de-risk approach and result, the query shapes and where they live, the dynamic-grid approach, the route/nav additions, and the test list. FM1: execution stubbed, engine not invented. FM2: safety/access model flagged open, not enforced. FM4: prove CodeMirror first. Surface what 7.4 to 7.5 leave underspecified (the execution contract, the safety model, the cross-tenant access) rather than inventing it. Return the plan and STOP."

## After approval

Execute, then verify: install / dev / build / test / lint / tsc strict all green with the count; report the CodeMirror de-risk result explicitly; an acceptance-criteria table for criteria 1 to 10; confirm execution is stubbed and the safety/access model is flagged open (not enforced); confirm non-ops denied; craft-bar look light + dark including the editor; no em-dashes, correct naming, tokens unchanged. One commit, subject "services/dis-ui: Slice 26 DuckDB Query Panel (ops, stubbed execution)", no Co-Authored-By. Do not push; show the diff summary and hash and stop.

## Carry-forward

- The heaviest open item for the batched Sanjeev message: the entire query engine + execution contract + safety model (read-only, row caps, timeouts, sandboxing) + cross-tenant SQL authorization for the bronze blob. Joins cross-tenant read (slices 24 to 25) and cross-tenant resubmit authorization (slice 25).
- After this, the ops cluster is complete. Remaining DIS UI work: Sources CRUD (slice 27, the last tenant screen).
