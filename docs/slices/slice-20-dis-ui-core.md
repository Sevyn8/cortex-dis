# Slice 20: DIS UI core (Phase 1 MVP)

**Status:** TODO.

**Phase:** 1 (UI track, builds on slice 19 foundation per build-guide section 2.2).

**Owners:** UI track. Scope drawn from the DIS UI Surface Map v1.0 (Phase 1) and the demand list.

---

## Goal

Build the Phase 1 MVP screens of the DIS UI on the slice 19 foundation, in fixture mode, so a single tenant has a demonstrable end-to-end path: upload a sample, review the proposed mapping, see quarantined rows and why they failed, trace a row by trace_id, and inspect mapping versions. This validates the architecture and the foundation before real dis-ui-server wiring (slice 13).

This slice builds five screens plus the navigation shell. It does NOT build the tenant dashboard, full sources CRUD, shadow rollout, notifications, or any ops surface. Those are Phase 2 and beyond.

## Hard constraints

1. **Build on the slice 19 foundation; stack unchanged.** Vite 8, React 19, TypeScript 6 (strict), Tailwind v4 (CSS-first), react-router 7, TanStack Query 5, jose 6, Vitest 4, pnpm. No new core stack additions without surfacing a real gap first.

2. **Fixture mode only.** Every screen reads and writes through `src/lib/dis-ui-server/*` against fixtures shaped to `docs/ui-engineer-demand-list.md`. Real mode stays wired but unimplemented (slice 13 gating, decisions.md D25). No real network calls in this slice.

3. **The demand list is a reconstruction.** `docs/ui-engineer-demand-list.md` is a faithful rebuild, not Sanjeev's original. Its onboarding shapes are recovered verbatim; the rest are provisional. Fixtures match it as written. Where a screen needs a field the demand list does not define, surface the gap; do not invent contract fields.

4. **Phase 1 scope reductions are strict.** Mapping Versions is read-only (no edit, create, or deprecate). Quarantine is the tenant slice, read plus detail only (no resubmit, no resolve). Audit is trace_id direct lookup only (no cross-tenant search, no filters). There is no Shadow Rollout Review, no Sources CRUD, no Dashboard, no Notifications, and no Ops surface in this slice.

5. **RBAC gates on the `dis:ops` role and tenant_id only.** Gating uses `isOps(snapshot)` (the `dis:ops` role) and `tenant_id`; no screen branches on a fine-grained permission array, and there is no `userType` claim (D25 open). Ops and cross-tenant surfaces must not be reachable by a non-ops persona.

6. **No backend modifications.** Pure UI. Do not edit `services/dis-ui-server/`, `services/identity-service/`, or any backend code.

7. **No archive mining.** Build every screen fresh. Do not reference or port from `archive/dis-legacy/` in admin-frontend (it is not on this machine, and porting is out of scope by decision).

8. **Repo hygiene.** No em-dashes in any repo file. Precise naming: DIS is the system, DIS UI is this frontend, dis-ui-server is the BFF. Strict TS, no `any`, tests land with the code, ESLint and Prettier clean.

9. **Git discipline.** Claude Code does not push; a pre-push hook enforces it; the operator reviews and pushes. No Co-Authored-By trailers. One coherent commit per checkpoint.

## Acceptance criteria

1. The app shell renders a tenant sidebar with the Phase 1 navigation (Upload, Quarantine, Audit, and Mappings reached via a source); ops-only items are not shown to a non-ops persona (one without the `dis:ops` role).
2. All Phase 1 routes are registered and wrapped in `AuthBoundary`; deep links resolve; an unknown route shows a not-found state.
3. Reusable Loading, Empty, Error, and PermissionDenied state components exist and are used across the screens (surface map 6.4). Screens do not roll their own.
4. A minimal read-only Sources index renders fixture sources (demand list 1.3) and links each to its Mappings. This is a navigation backbone, not the Phase 2 Sources CRUD screen.
5. The fixture client is extended with the Phase 1 endpoint stubs (onboarding, mappings, quarantine, audit, sources) returning shapes per the demand list; fixture mode is the default; no network calls occur.
6. `pnpm install`, `pnpm dev`, `pnpm build`, `pnpm test`, `pnpm lint`, and `pnpm tsc --noEmit` strict all pass; the slice 19 AuthBoundary and `/dev/login` are unaffected.
7. Sample Upload (route `/upload`): a drop zone or browse, source name and type and attach-to controls, and an "Analyze sample" action that calls the fixture client (demand list 2.1) and shows received, analyzing, and ready states (2.2).
8. Mapping Review (route `/upload/{sample_id}/review`): renders per-column inferred type, sample values, confidence, proposed canonical column, and transforms; low-confidence rows are visually flagged; per-column override controls are present; "Dry-run preview" renders fixture canonical rows (2.4); "Approve to staged" calls the fixture (2.5).
9. The onboarding pair shares the sample_id flow; navigating Upload to Review works end to end against fixtures.
10. Quarantine Console, tenant slice (route `/quarantine`): the failed-row list (demand list 4.1) with source, error reason, failure stage, and time filters; a row detail panel (4.2) showing original payload, error context, and the processing mapping version. Resubmit and resolve are out of scope and are not rendered as functional actions.
11. Audit and Trace Lookup (route `/audit`): a trace_id direct lookup (demand list 5.1) renders the ordered per-stage lifecycle; a quarantined trace shows its quarantined terminal stage. Cross-tenant search and filters are out of scope.
12. Mapping Versions and CRUD, read-only (route `/sources/{source_id}/mappings`): the version list (demand list 3.1) with status badges and a version view (3.2) showing the full definition. Edit, create, and deprecate are out of scope.
13. Every Phase 1 screen renders its loading, empty, and error states.
14. A tenant persona (no `dis:ops` role) can complete the demonstrable path in fixture mode: Upload to Review to approve, then Quarantine to detail, then Audit by trace_id, then a source's Mappings.
15. Commit history shows the slice landing via coherent per-checkpoint commits on `main`.

## Failure-mode categories

Categories Claude Code must consider in plan mode. Specific handling shapes come from plan mode, not this doc.

**FM1: Fixture drift.** Fixtures must match the demand list shapes. Provisional shapes are acceptable but must stay identical to the demand list. If a screen needs a field the demand list does not define, surface it; do not invent a contract field.

**FM2: Scope creep.** The Phase 1 reductions in constraint 4 are strict. The plan for each screen must enumerate explicitly what the screen does NOT do this slice.

**FM3: RBAC over-gating.** Gate on the `dis:ops` role (`isOps`) and tenant_id only, not a `userType`. Do not build permission-array gating. A non-ops persona must not be able to reach an ops or cross-tenant surface, including by deep link.

**FM4: TanStack Query consistency.** Reuse the slice 19 query patterns (the single QueryClient is already mounted). Consistent query keys, loading and error handling per query. Do not re-architect the data layer.

**FM5: State-component reuse.** The four cross-cutting state components are built once in Checkpoint 1 and reused. Screens must not duplicate them.

**FM6: Routing and AuthBoundary.** New routes are wrapped in AuthBoundary. Source-scoped routes (`/sources/{id}/mappings`, `/upload/{id}/review`) handle a missing or unknown id with an empty or error state, not a crash.

## Plan-mode prompts per checkpoint

The slice is five checkpoints. Each gets its own plan-mode invocation, reconcile-first, operator review before execute, no push.

### Checkpoint 1: Shell, navigation, route skeleton, state primitives, fixture client

**Plan-mode prompt:**

> "Read `docs/skills/sevyn8-workflow/SKILL.md`, this slice doc, `services/dis-ui/CLAUDE.md`, the DIS UI Surface Map sections 5 and 6, and `docs/ui-engineer-demand-list.md`. Building on slice 19.
>
> Produce a plan to add:
> - the authenticated app shell and tenant sidebar with Phase 1 nav (Upload, Quarantine, Audit, Mappings via source), ops items hidden for TENANT
> - the Phase 1 route registry, all wrapped in AuthBoundary, plus a not-found route
> - reusable Loading, Empty, Error, PermissionDenied components (surface map 6.4)
> - a minimal read-only Sources index (demand list 1.3) as the nav backbone, linking to a source's Mappings; NOT the Phase 2 Sources CRUD screen
> - the fixture client extended with Phase 1 endpoint stubs and fixtures shaped to the demand list
> - tests: shell renders, nav gates by userType, each state component renders, sources index lists fixtures
>
> Return the file list under `src/`, the fixture shapes used, the nav-gating approach, and the test list. Foundation for the screens only; do not build Sample Upload, Quarantine, Audit, or Mapping Versions in this checkpoint."

### Checkpoint 2: Sample Upload and Mapping Review

**Plan-mode prompt:**

> "Building on Checkpoint 1. Read Surface Map screens 3 and 4, and demand list 2.1 to 2.5.
>
> Produce a plan to add:
> - Sample Upload at `/upload`: drop zone or browse, source name and type and attach-to, "Analyze sample" calling fixture 2.1, with received/analyzing/ready states polling 2.2
> - Mapping Review at `/upload/{sample_id}/review`: per-column table (inferred type, sample values, confidence, proposed canonical, transforms), low-confidence flagging, per-column overrides (2.3), dry-run preview (2.4), Approve to staged (2.5)
> - tests: fixture upload returns sample_id, analysis renders columns, low-confidence flagged, dry-run renders rows, approve transitions state
>
> Return the file list, the fixture interactions per action, and how FM2 scope reductions apply (no shadow review, no real backend). Do not build Quarantine, Audit, or Mapping Versions here."

### Checkpoint 3: Quarantine Console (tenant slice)

**Plan-mode prompt:**

> "Building on Checkpoints 1 to 2. Read Surface Map screen 7 (tenant slice only) and demand list 4.1 to 4.2.
>
> Produce a plan to add:
> - Quarantine Console at `/quarantine`: failed-row list (4.1) with source, error reason, failure stage, time and status filters; row detail panel (4.2) with original payload, error context, processing mapping version
> - tests: list renders fixtures, filters narrow the list, row detail opens
>
> Return the file list and the fixture shapes. FM2: resubmit and resolve are NOT functional this slice; render them disabled or omit them, and say which. Tenant slice only; do not add the ops cross-tenant view."

### Checkpoint 4: Audit and Trace Lookup (trace_id direct)

**Plan-mode prompt:**

> "Building on Checkpoints 1 to 3. Read Surface Map screen 8 and demand list 5.1.
>
> Produce a plan to add:
> - Audit and Trace Lookup at `/audit`: a trace_id input and direct lookup (5.1) rendering the ordered per-stage lifecycle; a quarantined trace shows its quarantined terminal stage with error_code
> - tests: known trace_id renders the lifecycle, a quarantined trace renders the terminal stage, an unknown trace_id renders the empty state
>
> Return the file list and the lifecycle rendering approach. FM2: no cross-tenant search and no filters this slice (Phase 3). trace_id direct lookup only."

### Checkpoint 5: Mapping Versions and CRUD (read-only)

**Plan-mode prompt:**

> "Building on Checkpoints 1 to 4. Read Surface Map screen 6 and demand list 3.1 to 3.2.
>
> Produce a plan to add:
> - Mapping Versions at `/sources/{source_id}/mappings`: the version list (3.1) with active, staged, deprecated badges; a version view (3.2) showing the full definition; a link to Audit filtered by mapping_version_id
> - tests: version list renders with badges, version view opens, unknown source id renders the empty or error state
>
> Return the file list. FM2: read-only. No edit, no create new version, no deprecate this slice. The 'Edit' and 'New version' affordances are out of scope; render disabled or omit, and say which."

## Out of scope

- Tenant Dashboard, Sources and Connections full CRUD, Shadow Rollout Review, Notifications (Phase 2)
- Ops Fleet, cross-tenant Quarantine, cross-tenant and filtered Audit, DuckDB Query Panel (Phase 3)
- Quarantine resubmit and resolve actions; mapping edit, create, and deprecate (later phases)
- Real dis-ui-server calls (slice 13); real Customer Master OIDC (D25)
- Production deploy, CI workflow, Playwright e2e (later slices)
- Mobile responsiveness, internationalization (deferred per Surface Map 6.3)

## Companion artifacts

- `dis-ui-surface-map.md` (Surface Map v1.0; the screen designs, journeys, wireframes, RBAC table)
- `docs/ui-engineer-demand-list.md` (the dis-ui-server endpoint contract; a reconstruction, shapes provisional)
- `docs/slices/slice-19-ui-foundation.md` (the foundation this builds on)
- `services/dis-ui/docs/dis-ui-server-contract.md` (the in-repo contract record and open questions)

## References

- `docs/build-guide.md` section 6.1 (10-step build loop), 6.4 (CLAUDE.md hygiene)
- `docs/architecture.md` section 4.13 (DIS UI), section 4.17 (dis-ui-server handlers)
- `docs/decisions.md` D22 (mapping_version_id on canonical rows), D25 (Customer Master, claim vocabulary open), D26 (BFF), D36 (CSV upload split)
- `services/dis-ui/CLAUDE.md`, `README.md`

## Notes for future slices

- Slice 21 and beyond complete Phase 2 (Dashboard, Sources CRUD, Shadow Rollout Review, Notifications, Quarantine resubmit) and Phase 3 (ops surfaces).
- When slice 13 lands dis-ui-server, the fixture-mode switch flips to real-call mode; the dis-ui-server contract doc is the bridge, and the provisional demand-list shapes get reconciled against Sanjeev's slices 15 to 17.
- The demand list is a reconstruction. If Sanjeev's original or his slice 15 to 17 implementation surfaces a different shape, the fixtures and this slice's screens reconcile to it.
