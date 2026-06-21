# Slice 21: DIS UI Phase 2, contract-safe screens (Tenant Dashboard + Notifications)

**Status:** TODO.

**Phase:** 2 (UI track), the subset that does not depend on open backend contracts. Builds on slice 20.

**Owners:** UI track. Scope drawn from the DIS UI Surface Map v1.0 (Phase 2) and the demand list.

---

## Goal

Build the two Phase 2 screens that are safe to build on fixtures today, the Tenant Dashboard and Notifications, on the completed slice 20 foundation, in fixture mode. These two read summaries and lists the UI can model without the open source-registration, shadow-rollout, or resubmit contracts. The Tenant Dashboard becomes the tenant landing at `/`, replacing the slice 20 redirect to `/sources`.

This slice deliberately takes only two of the five Phase 2 screens. The other three (Sources CRUD, Shadow Rollout Review, Quarantine resubmit) are held for a later slice because they depend on contracts that are still open (see Out of scope and Carry-forward).

## Hard constraints

1. **Build on the slice 20 foundation; stack unchanged.** Vite 8, React 19, TypeScript 6 (strict), Tailwind v4 (CSS-first), react-router 7, TanStack Query 5, jose 6, Vitest 4, pnpm. No new core stack additions without surfacing a real gap first.

2. **Fixture mode only.** Both screens read through `src/lib/dis-ui-server/*` against fixtures shaped to `docs/ui-engineer-demand-list.md`. Real mode stays wired but unimplemented (slice 13 gating). No real network calls.

3. **Contract-safe scope only.** This slice builds Tenant Dashboard and Notifications. It does NOT build Sources CRUD (depends on the open source-registration contract), Shadow Rollout Review (depends on the open shadow and promote contracts), or Quarantine resubmit (depends on the open resubmit request shape). Those are a later slice. If a screen here needs one of those contracts, surface it and stop, do not build across the fence.

4. **RBAC gates on the `dis:ops` role and tenant_id only.** Gating uses `isOps(snapshot)`; no screen branches on a fine-grained permission array (D25 open). Both screens here are tenant-scoped. No ops or cross-tenant surface.

5. **No backend modifications.** Pure UI. Do not edit `services/dis-ui-server/`, `services/identity-service/`, or any backend code.

6. **Provisional shapes stay flagged.** The demand list shapes for `dashboard/summary` (1.2) and notifications (6.1 to 6.4) are provisional. Fixtures match the demand list as written; where a screen needs a field the demand list does not define, surface the gap, do not invent a contract field.

7. **Repo hygiene.** No em-dashes in any repo file. Precise naming: DIS is the system, DIS UI is this frontend, dis-ui-server is the BFF. Strict TS, no `any`, tests land with the code, ESLint and Prettier clean.

8. **Git discipline.** Claude Code does not push; the pre-push hook enforces it; the operator reviews and pushes. No Co-Authored-By trailers. One coherent commit per checkpoint.

## Acceptance criteria

1. Tenant Dashboard renders at `/` (route `/`); the slice 20 redirect from `/` to `/sources` is removed; `/sources` remains its own route reachable from the sidebar.
2. The Dashboard reads `GET /v1/dashboard/summary` (demand list 1.2) via the fixture client and renders the per-source health rollup (last submission, quarantined-open count, rows in window, health) and the latency snapshot.
3. The Dashboard renders Loading, Empty, and Error states via the shared primitives; it does not roll its own.
4. The Dashboard is tenant-scoped to the persona; no cross-tenant data; no ops widgets.
5. Notifications renders at `/notifications`, registered under AuthBoundary and reachable from the sidebar; the sidebar gains a Notifications item.
6. The Notifications list reads `GET /v1/notifications` (demand list 6.2) and renders severity, text, source, time, and read state, with a filter (unread, all, errors).
7. A bell indicator in the header reads `GET /v1/notifications/unread-count` (6.1) and shows the unread badge; marking a notification read (6.3) and mark-all-read (6.4) update the count.
8. Notifications renders Loading, Empty, and Error states via the shared primitives.
9. `pnpm install`, `pnpm dev`, `pnpm build`, `pnpm test`, `pnpm lint`, and `pnpm tsc --noEmit` strict all pass; the slice 20 screens and the auth path are unaffected.
10. The stray-file artifacts (`dis-ui@0.0.0`, `vite`) are added to `.gitignore` so they can never be staged.
11. Commit history shows the slice landing via coherent per-checkpoint commits on `main`.

## Failure-mode categories

**FM1: Crossing the contract fence.** This slice is the two contract-safe screens only. If the Dashboard or Notifications work pulls in Sources CRUD, shadow, resubmit, or a source-registration need, stop and surface it; do not build the dependent screen.

**FM2: Fixture drift.** Fixtures match the demand list shapes (1.2, 6.1 to 6.4). Provisional is fine; invented contract fields are not. Surface a missing field rather than inventing it.

**FM3: The `/` migration.** Moving the Dashboard to `/` removes the slice 20 redirect. Confirm `/sources` still resolves on its own and that deep links and the not-found route are unaffected. Do not leave a dangling redirect or a dead reference.

**FM4: RBAC.** Gate on `isOps` and tenant_id only. Both screens are tenant-scoped. No ops widgets, no cross-tenant data, including by deep link.

**FM5: State and query patterns.** Reuse the slice 20 state primitives and TanStack Query patterns (consistent query keys, the single QueryClient). Do not re-architect or duplicate.

**FM6: Header surface.** The bell lives in the existing header alongside the reconciled profile email. Do not rebuild the header; extend it.

## Plan-mode prompts per checkpoint

Two checkpoints, reconcile-first, plan mode the one stop, operator reviews and pushes, no push by Claude Code.

### Checkpoint 1: Tenant Dashboard at `/`

**Plan-mode prompt:**

> "Read `docs/skills/sevyn8-workflow/SKILL.md`, this slice doc, `services/dis-ui/docs/dis-ui-surface-map.md` screen 1 Tenant Dashboard (read its authority banner first), `docs/ui-engineer-demand-list.md` 1.2, `services/dis-ui/CLAUDE.md`, and your slice 20 code (`src/lib/dis-ui-server/`, the state components, the route table, the existing redirect at `/`).
>
> Produce a plan to:
> - build the Tenant Dashboard at route `/`, replacing the redirect to `/sources`; keep `/sources` as its own route
> - read `dashboard/summary` (1.2) via the fixture client: per-source health rollup and latency snapshot; tenant-scoped to the persona
> - use the shared Loading, Empty, Error states
> - enrich the dashboard fixture from any Checkpoint 1 stub to the 1.2 shape, grounded on tenant `t_acme9k2l1mn4` and the composite source_id (`manual_csv_upload`, no invented `src_*`)
> - add `dis-ui@0.0.0` and `vite` to `.gitignore`
> - tests: dashboard renders the rollup and latency; empty and error states; `/` resolves to the dashboard and `/sources` still resolves; not-found unaffected
>
> Return the file list, the dashboard fixture shape, the routing change, and the test list. FM1: Dashboard only; no Sources CRUD, no Notifications (Checkpoint 2), no ops widgets. Surface any shape missing from the demand list rather than inventing it. Return the plan and STOP."

### Checkpoint 2: Notifications

**Plan-mode prompt:**

> "Building on Checkpoint 1. Read `services/dis-ui/docs/dis-ui-surface-map.md` screen 9 Notifications and `docs/ui-engineer-demand-list.md` 6.1 to 6.4.
>
> Produce a plan to:
> - build Notifications at `/notifications` (under AuthBoundary, sidebar item added): the list (6.2) with severity, text, source, time, read state, and a filter (unread, all, errors)
> - add a bell indicator to the existing header reading unread-count (6.1); marking read (6.3) and mark-all-read (6.4) update the count
> - use the shared Loading, Empty, Error states
> - enrich the notifications fixtures to the 6.1 to 6.4 shapes, grounded on the tenant persona
> - tests: list renders with severity and read state; filter narrows the list; the bell shows the unread count; mark-read and mark-all-read update it; empty and error states
>
> Return the file list, the notifications fixture shapes, the header change, and the test list. FM1/FM6: extend the header, do not rebuild it; Notifications only; no Sources CRUD, shadow, or resubmit. Surface any missing shape rather than inventing it. Return the plan and STOP."

## Out of scope

- Sources and Connections full CRUD (create, edit, deprecate, detail), Shadow Rollout Review, Quarantine resubmit and resolve (the contract-dependent Phase 2 screens, held for a later slice)
- Ops Fleet, cross-tenant views, DuckDB Query Panel (Phase 3)
- Real dis-ui-server calls (slice 13); real Customer Master OIDC (D25)
- Production deploy, CI workflow, Playwright e2e
- Mobile responsiveness, internationalization (deferred per Surface Map 6.3)

## Companion artifacts

- `services/dis-ui/docs/dis-ui-surface-map.md` (Surface Map v1.0; screens 1 and 9, with the authority banner)
- `docs/ui-engineer-demand-list.md` (the dis-ui-server endpoint contract; a reconstruction, shapes provisional)
- `docs/slices/slice-20-dis-ui-core.md` (the Phase 1 foundation this builds on)
- `services/dis-ui/docs/dis-ui-server-contract.md` (the in-repo contract record and open questions)

## References

- `docs/build-guide.md` section 6.1 (build loop), 6.4 (CLAUDE.md hygiene)
- `docs/architecture.md` section 4.13 (DIS UI), 4.17 (dis-ui-server handlers)
- `docs/decisions.md` D25 (Customer Master claim vocabulary, open), D37 (external id vs UUID, open)
- `services/dis-ui/CLAUDE.md`, `README.md`

## Carry-forward

- The held Phase 2 screens (Sources CRUD, Shadow Rollout Review, Quarantine resubmit) become a later slice once the open contracts land: source registration and allocation, the resubmit request shape, and the shadow and promote contracts.
- Open items pending the Sanjeev coordination message (deferred by choice, to be batched): source identity and registration; the attach-to-existing field on onboarding 2.1; the canonical-column vocabulary; the RBAC claim vocabulary (D25); the profile / GET /me call; the absent `contracts/customer-master/`. Plus the demand-list illustrative-id sweep (stale `src_*` and `ten_*` examples) before that message goes out.
- When slice 13 lands dis-ui-server, fixture mode flips to real-call mode; the dis-ui-server contract doc is the bridge, and the provisional shapes reconcile against Sanjeev's slices 15 to 17.
