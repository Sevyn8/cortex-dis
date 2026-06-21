# Slice 24: Ops Fleet (the first ops surface)

**Status:** TODO.

**Phase:** Phase 3 (ops cluster). The first ops-gated screen. Builds on slices 20 to 23.

**Owners:** UI track owns the screen and its API shapes; Sanjeev owns the cross-tenant access policy (RLS) the screen depends on.

## Goal

Build Ops Fleet, the cross-tenant tenant-health screen for ops users (demand list 7.1 to 7.3), as the first real ops surface in the DIS UI. This slice also does two things that the ops surface has never needed before: it wires the ops route-guard for real (a non-ops persona reaching an ops route gets PermissionDenied), and it gives the ops persona a landing (the root route redirects ops to Fleet, since the Tenant Dashboard is tenant-scoped and empty for a null-tenant ops user).

Built in the slice 23 design system at the craft bar (carded tables, badges, metric cards, the branded shell), light and dark.

## The cross-tenant dependency (named up front)

Fleet is cross-tenant, and cross-tenant ops read is the thinnest-specified part of the system. The UI owns and defines the fleet screen and the GET /v1/ops/fleet/* API shapes it needs (per the division of authority: the UI decides functionality and API shape). But whether an ops token is actually permitted to read across tenants, and how that isolation is enforced, is Sanjeev's RLS policy, and it is not yet defined. The Customer Master read contract (ithina_master_db_read_access.md) defines the PLATFORM-vs-TENANT duality (PLATFORM context, tenant_id NULL) that ops cross-tenant access would use, but it does not define the fleet read itself.

So this slice is built on fixtures with the containment pattern, and its reconciliation debt is the heaviest of any slice so far, because the contract it stands in for barely exists. Every fleet shape is a flagged provisional constant in a single containment file (ops-fleet.ts), and the cross-tenant access model is recorded as a first-class open item for the batched Sanjeev message. This is deliberate: the operator chose to build Fleet now and accept this debt.

## Scope

In:
- Ops Fleet screen at /ops/fleet, ops-gated, reading fleet summary + per-tenant health (7.1 to 7.3) from a fixture
- the ops route-guard: a non-ops persona reaching any /ops/* route renders PermissionDenied (the component built in slice 20, never wired), not a broken tenant screen
- the ops landing: the root route / redirects an ops persona to /ops/fleet; a tenant persona still lands on the Tenant Dashboard, unchanged
- the ops nav group becomes visible and active for ops (the isOps-gated group that until now had no items)

Out (later ops slices):
- DuckDB Query Panel (7.4 to 7.5)
- cross-tenant Quarantine, Audit search (5.2)
- any drill-down from a fleet row into a specific tenant (note the seam, do not build it)

## Hard constraints

1. **UI owns the shapes; Sanjeev owns the policy.** The UI defines the fleet API shapes; cross-tenant read permission and enforcement is Sanjeev's RLS call, recorded as open, not invented.
2. **Containment.** Every fleet shape (summary, per-tenant row, any detail) is a flagged provisional constant in src/lib/dis-ui-server/ops-fleet.ts, naming demand list 7.1 to 7.3. The screen consumes typed values only. Single reconciliation point.
3. **Tenant path unchanged (FM1).** The root-route redirect branches on isOps: ops to /ops/fleet, tenant stays on the Tenant Dashboard. The existing "renders the Tenant Dashboard at the index" behavior holds for a tenant persona. A new test covers the ops redirect.
4. **Guard fails closed.** isOps is the only gate (it already exists, fails closed). The route-guard denies non-ops access to /ops/* via PermissionDenied. No new auth logic; reuse isOps and the existing AuthBoundary pattern.
5. **Design system at the craft bar.** Built on the slice 23 primitives and the craft spec: carded per-tenant table with badges for health, metric cards for the fleet summary, the shared state components, light and dark. No raw HTML.
6. **No backend, no contract edits, no canonical changes.** Pure UI on fixtures. Tokens unchanged.
7. **Repo hygiene + git discipline.** No em-dashes; precise DIS / DIS UI / dis-ui-server; strict TS no any; ESLint/Prettier clean. Claude Code does not push; operator reviews and pushes; no Co-Authored-By; one coherent commit per checkpoint.

## Acceptance criteria

1. /ops/fleet exists, registered in the route table under the ops guard, and renders for an ops persona.
2. The screen reads a fleet summary (7.1: tenant count, healthy/warning/failing counts, total rows, open quarantine across the fleet) rendered as metric cards.
3. The screen reads per-tenant health (7.2 to 7.3: tenant, health, rows, open quarantine, last activity) rendered as a carded table with health as a Badge.
4. A non-ops (tenant) persona navigating to /ops/fleet (or any /ops/*) renders PermissionDenied, not the screen and not a crash.
5. An ops persona landing on / is redirected to /ops/fleet; a tenant persona landing on / still sees the Tenant Dashboard (unchanged).
6. The ops nav group is visible and navigable for ops, hidden for tenant (existing isOps gating, now with a real destination).
7. All fleet shapes are flagged provisional constants in ops-fleet.ts; the screen consumes typed values only.
8. Empty/loading/error states via the shared components; the screen looks like the slice 23 craft bar in light and dark.
9. pnpm install, dev (200, no console errors), build, test, lint, tsc --noEmit strict all green; the full prior suite stays green (selector updates only where shared routing changed; the tenant index behavior unchanged).
10. No em-dashes; correct naming; grounded ids and canonical vocabulary; tokens unchanged.

## Failure modes

- **FM1: Tenant path regression.** The root-route redirect must not change the tenant landing. The existing index test holds for tenant; a new test covers ops. If the tenant index behavior assertion would change, stop and surface.
- **FM2: Shape leakage.** Fleet shapes stay flagged constants in ops-fleet.ts, not threaded into the screen. Single reconciliation point.
- **FM3: Inventing the cross-tenant policy.** The UI defines the fleet API shape; it does NOT invent how cross-tenant read is authorized or enforced. That is Sanjeev's RLS policy, recorded as open. Surface anything 7.1 to 7.3 leaves underspecified (the health derivation, the activity field, any drill-down target) rather than inventing it.
- **FM4: Guard gaps.** The guard must cover all /ops/* routes, not just Fleet, so future ops screens inherit it. A non-ops deep link fails closed to PermissionDenied.
- **FM5: Scope creep.** No DuckDB, no cross-tenant Quarantine/Audit, no per-tenant drill-down this slice. Note seams; do not build them.

## Plan-mode prompt (single checkpoint)

> "Read docs/skills/sevyn8-workflow/SKILL.md, this slice doc, services/dis-ui/docs/dis-ui-visual-craft-spec.md (the craft bar), services/dis-ui/docs/dis-ui-surface-map.md (the Ops Fleet screen and the ops cluster), docs/ui-engineer-demand-list.md 7.1 to 7.3, services/dis-ui/CLAUDE.md, and your current code: the route table (AppRoutes.tsx) and its index/Tenant-Dashboard test, src/auth (isOps, useAuth, AuthBoundary), the PermissionDenied state component (built, never wired), the ops dev persona in the dev-login/fixtures (tenant_id null), the Sidebar's isOps nav group, and the slice 23 primitives + the shadow/notifications mutable-fixture patterns for the containment style.
>
> Produce a plan to:
> 1. add src/lib/dis-ui-server/ops-fleet.ts with the fleet summary and per-tenant-health shapes as flagged provisional constants naming demand list 7.1 to 7.3 (FM2); fixture data for a small multi-tenant fleet
> 2. build the Ops Fleet screen at /ops/fleet at the craft bar: a fleet-summary row of metric cards (7.1), a carded per-tenant table (7.2 to 7.3) with health as a Badge, the shared state components, light and dark
> 3. wire the ops route-guard: an /ops/* route element (or layout route) that renders PermissionDenied when isOps is false, covering all future ops routes not just Fleet (FM4); register /ops/fleet under it
> 4. add the ops landing redirect: the index route / branches on isOps, redirecting ops to /ops/fleet while a tenant persona still renders the Tenant Dashboard (FM1); keep the existing tenant index test green and add an ops-redirect test
> 5. the ops nav group (already isOps-gated in the Sidebar) now points to /ops/fleet; confirm it shows for ops and stays hidden for tenant
> 6. tests: Fleet renders summary + table for ops; non-ops to /ops/fleet renders PermissionDenied; ops / redirects to Fleet; tenant / still renders Dashboard; nav group visibility by persona; fleet shapes are flagged constants consumed as typed values
>
> Return the file list, the fleet shapes and where they live, the guard approach (per-route vs layout route), the index-redirect approach, and the test list (explicitly: which existing tests change and whether selector-only, and the tenant index assertion must NOT change). FM3: surface what 7.1 to 7.3 leave underspecified rather than inventing it. Return the plan and STOP."

## After approval

Execute, then verify: install / dev / build / test / lint / tsc strict all green with the count; an acceptance-criteria table for criteria 1 to 10; confirm the tenant index behavior unchanged, non-ops denial works, ops lands on Fleet; confirm craft-bar look in light and dark; no em-dashes, correct naming, tokens unchanged. One commit, subject "services/dis-ui: Slice 24 Ops Fleet, ops guard and landing", no Co-Authored-By. Do not push; show the diff summary and hash and stop.

## Carry-forward

- Cross-tenant ops read (whether/how an ops token reads across tenants) is Sanjeev's RLS policy and is the heaviest open item from this slice; it goes to the batched Sanjeev message as a first-class question, alongside the fleet API shapes the UI now defines.
- Remaining ops cluster after this: DuckDB Query Panel (7.4 to 7.5), cross-tenant Quarantine, Audit search (5.2), all now inheriting the ops guard built here.
- Remaining tenant work: Sources CRUD (UI defines the register-a-source API, conforming to the composite source schema).
