# Slice 22: DIS UI Phase 2, contract-dependent action screens (Quarantine resubmit + Shadow Rollout Review)

**Status:** TODO.

**Phase:** 2 (UI track), the contract-dependent subset. Builds on slices 20 and 21.

**Owners:** UI track. Scope drawn from the DIS UI Surface Map v1.0 (screens 5 and 7 actions) and the demand list.

---

## Goal

Build the two Phase 2 action surfaces that depend on backend contracts that are still open: the Quarantine resubmit action (on the existing Quarantine console) and the Shadow Rollout Review screen. This slice is a deliberate, eyes-open exception to the slice 21 contract-fence: we are choosing to build these now on provisional contracts, accepting reconciliation later, because their UI value is wanted before the contracts land.

Because the premise is provisional contracts, the strategy is containment: every provisional shape lives in the fixture layer as a flagged constant, so when Sanjeev pins the real contracts the reconciliation is fixture and display work, not a screen rewrite. See Reconciliation debt.

## Risk asymmetry (read this before building)

The two screens are not equal risk and the slice treats them differently:
- **Checkpoint 1, Quarantine resubmit:** low blast radius. The screen exists (slice 20 Checkpoint 3); this enables one action against one endpoint (demand list 4.3, roughly sketched). Reconciliation is localized to a single action handler and its fixture.
- **Checkpoint 2, Shadow Rollout Review:** high blast radius. A new screen built on four provisional contracts (demand list 2.6 to 2.9), none pinned. When the shadow and promote contracts land, its shapes and possibly its flow may change materially. Containment keeps that to the fixture layer.

## Hard constraints

1. **Build on the slices 20 and 21 foundation; stack unchanged.** Vite 8, React 19, TypeScript 6 (strict), Tailwind v4 (CSS-first), react-router 7, TanStack Query 5, jose 6, Vitest 4, pnpm. No new core stack additions without surfacing a real gap first.

2. **Fixture mode only.** Both surfaces read and write through `src/lib/dis-ui-server/*` against fixtures shaped to `docs/ui-engineer-demand-list.md`. Real mode stays wired but unimplemented (slice 13 gating). No real network calls.

3. **Provisional contracts, contained.** This slice builds on open contracts: resubmit (4.3) and shadow (2.6 to 2.9). Every shape that is not pinned in `docs/ui-engineer-demand-list.md`, `libs/dis-canonical`, or `libs/dis-core` must be a flagged provisional constant in the fixture layer, not spread through screen logic, so it has one reconciliation point. Do not invent fields silently; flag each one and where it lives.

4. **RBAC gates on the `dis:ops` role and tenant_id only.** Gating uses `isOps(snapshot)`; no screen branches on a fine-grained permission array (D25 open). Both surfaces are tenant-scoped. Resolve (ops-only, scope P) stays out of scope.

5. **No backend modifications.** Pure UI. Do not edit `services/dis-ui-server/`, `services/identity-service/`, or any backend code.

6. **Reconciliation discipline.** Reuse the canonical-column vocabulary (`CANONICAL_COLUMNS`) and UUIDv7 trace ids already aligned to slices 03/04. Any new trace or source reference uses the existing grounded ids (`t_acme9k2l1mn4`, composite source_id, the shared `QUARANTINE_TRACE_IDS`).

7. **Repo hygiene.** No em-dashes in any repo file. Precise naming: DIS is the system, DIS UI is this frontend, dis-ui-server is the BFF. Strict TS, no `any`, tests land with the code, ESLint and Prettier clean.

8. **Git discipline.** Claude Code does not push; the pre-push hook enforces it; the operator reviews and pushes. No Co-Authored-By trailers. One coherent commit per checkpoint.

## Acceptance criteria

1. On the existing Quarantine console, a quarantined row's detail offers a Resubmit action (previously rendered disabled in slice 20) that opens a confirm with a resubmit type choice (replay or fixed_file).
2. Confirming resubmit calls the fixture client for `POST /quarantine/{trace_id}/resubmit` (demand list 4.3) with `resubmit_type` and `parent_trace_id`; the fixture records the resubmit and the row reflects the new state.
3. The resubmit respects the chain-depth cap of 3 (architecture 6.5): a row already at depth 3 shows the action disabled with the reason.
4. Resolve remains out of scope (ops-only, scope P); it is not rendered as a functional action.
5. Shadow Rollout Review renders at `/sources/:sourceId/shadow`, registered under AuthBoundary and reached from a source that has a staged mapping version (a link from the Mapping Versions screen or the sources index where a staged version exists).
6. The screen reads the shadow-stats rollup (demand list 2.6): window, input chunks, staged rows, validation pass rate, diff-vs-active counts.
7. The screen reads a shadow-diff sample (2.7) and renders staged-vs-active rows.
8. Promote (2.8) and Reject (2.9) actions call the fixture client; promote transitions the staged version to active, reject transitions it to deprecated; both update the fixture state.
9. Both surfaces use the shared Loading, Empty, and Error primitives; a source with no staged version renders the empty state, not a crash.
10. Every provisional shape (resubmit request/response, shadow-stats, shadow-diff, promote/reject results) is a flagged constant in the fixture layer with a comment naming the open contract it stands in for.
11. `pnpm install`, `pnpm dev`, `pnpm build`, `pnpm test`, `pnpm lint`, and `pnpm tsc --noEmit` strict all pass; the slice 20 and 21 screens and the auth path are unaffected.
12. Commit history shows the slice landing via coherent per-checkpoint commits on `main`.

## Failure-mode categories

**FM1: Silent invention.** The resubmit and shadow shapes are provisional. Every field not pinned in the demand list or Sanjeev's libs must be a flagged fixture constant with a naming comment. If a screen needs a field with no source, surface it; do not invent it into screen logic where it cannot be found later.

**FM2: Blast-radius creep.** Keep provisional shapes in the fixture layer, not threaded through components, so reconciliation is one edit per contract. Screen logic consumes typed shapes; it does not hardcode provisional values.

**FM3: Chain-depth and scope.** Resubmit respects the depth-3 cap (arch 6.5). Resolve stays out (ops-only). No cross-tenant, no ops surface, including by deep link.

**FM4: Shadow entry and state.** Shadow Review is reached only from a source with a staged version; a source without one renders empty. Promote and reject mutate the staged version's state in the fixture (like the notifications mutable store, isolated behind a reset shim for tests).

**FM5: Reuse, do not re-architect.** Reuse the slice 20/21 state primitives, TanStack Query patterns, the mutable-fixture pattern from notifications, and the grounded ids and canonical vocabulary. No new stack, no parallel patterns.

**FM6: Mutation invalidation.** Resubmit, promote, and reject change state the other screens read (Quarantine list, Mapping Versions). Use shared-prefix query invalidation so the dependent views refetch, as the notifications bell/list does.

## Plan-mode prompts per checkpoint

Two checkpoints, reconcile-first, plan mode the one stop, operator reviews and pushes, no push by Claude Code.

### Checkpoint 1: Quarantine resubmit

**Plan-mode prompt:**

> "Read `docs/skills/sevyn8-workflow/SKILL.md`, this slice doc, `services/dis-ui/docs/dis-ui-surface-map.md` screen 7 Quarantine (the resubmit action, read its authority banner first), `docs/ui-engineer-demand-list.md` 4.3, `docs/architecture.md` 6.5 (resubmit chain depth), `services/dis-ui/CLAUDE.md`, and your existing Quarantine code (`src/routes/QuarantineConsole.tsx`, `src/lib/dis-ui-server/quarantine.ts` and its tests, where resubmit is currently rendered disabled).
>
> Produce a plan to:
> - enable the Resubmit action on a quarantined row's detail: a confirm with a resubmit_type choice (replay or fixed_file)
> - call the fixture client for `POST /quarantine/{trace_id}/resubmit` (4.3) with resubmit_type and parent_trace_id; record the resubmit in the fixture so the row reflects the new state (mutable-fixture pattern, reset shim for tests)
> - enforce the chain-depth-3 cap (arch 6.5): a row at depth 3 shows the action disabled with the reason
> - keep Resolve out (ops-only); invalidate the quarantine query so the list refetches
> - flag the resubmit request and response as provisional fixture constants naming demand-list 4.3
> - tests: resubmit opens the confirm, calls the fixture with the right body, the row updates, depth-3 disables the action, resolve absent
>
> Return the file list, the provisional resubmit shapes and where they live, and the test list. FM1/FM2: provisional shapes in the fixture layer only. Surface anything 4.3 leaves underspecified rather than inventing it. Return the plan and STOP."

### Checkpoint 2: Shadow Rollout Review

**Plan-mode prompt:**

> "Building on Checkpoint 1. Read `services/dis-ui/docs/dis-ui-surface-map.md` screen 5 Shadow Rollout Review and `docs/ui-engineer-demand-list.md` 2.6 to 2.9.
>
> Produce a plan to:
> - build Shadow Rollout Review at `/sources/:sourceId/shadow` (under AuthBoundary, registered in the route table), reached from a source that has a staged mapping version (add the entry link from Mapping Versions or the sources index)
> - read shadow-stats (2.6): window, input chunks, staged rows, validation pass rate, diff-vs-active counts
> - read a shadow-diff sample (2.7): staged-vs-active rows
> - Promote (2.8) transitions staged to active; Reject (2.9) transitions staged to deprecated; both mutate the fixture (mutable-fixture pattern, reset shim)
> - a source with no staged version renders the empty state
> - flag every shadow shape (stats, diff, promote, reject) as provisional fixture constants naming demand-list 2.6 to 2.9
> - invalidate the mappings query so Mapping Versions reflects a promote or reject
> - tests: stats and diff render; promote moves staged to active; reject moves staged to deprecated; no-staged-version renders empty; the mappings view reflects the change
>
> Return the file list, the provisional shadow shapes and where they live, the entry-link change, and the test list. FM1/FM2: all four shapes are provisional fixture constants; keep them out of screen logic. FM3: tenant-scoped, no ops surface. Surface anything 2.6 to 2.9 leaves underspecified rather than inventing it. Return the plan and STOP."

## Out of scope

- Sources and Connections full CRUD (still held; depends on the open source-registration contract)
- Quarantine resolve (ops-only, scope P)
- Ops Fleet, cross-tenant views, DuckDB Query Panel (Phase 3)
- Real dis-ui-server calls (slice 13); real Customer Master OIDC (D25)
- Production deploy, CI workflow, Playwright e2e
- Mobile responsiveness, internationalization

## Reconciliation debt (accepted, to revisit when Sanjeev pins the contracts)

This slice knowingly builds on open contracts. When each lands, revisit:
- **Resubmit (4.3):** the request body (resubmit_type values, fixed_file payload handling) and response shape; the depth-3 rule confirmed against the real backend.
- **Shadow-stats and shadow-diff (2.6, 2.7):** the rollup fields and the diff row shape are provisional fixtures.
- **Promote and reject (2.8, 2.9):** the transition semantics and any side effects (the `mapping.changed` event) are modeled in fixtures only.
- All of the above are flagged constants in the fixture layer; reconciliation should be fixture and display edits, not screen rewrites. If a contract lands materially different from the fixture, that screen is the reconciliation unit.

## Companion artifacts

- `services/dis-ui/docs/dis-ui-surface-map.md` (Surface Map v1.0; screens 5 and 7, with the authority banner)
- `docs/ui-engineer-demand-list.md` (the dis-ui-server endpoint contract; a reconstruction, these shapes provisional)
- `docs/slices/slice-20-dis-ui-core.md`, `docs/slices/slice-21-dis-ui-phase2-safe.md` (the foundation this builds on)
- `services/dis-ui/docs/dis-ui-server-contract.md` (the in-repo contract record and open questions)

## References

- `docs/build-guide.md` section 6.1, 6.4
- `docs/architecture.md` 4.13 (DIS UI), 4.17 (dis-ui-server handlers), 6.5 (resubmit chain depth)
- `docs/decisions.md` D22 (mapping version pinning), D25 (Customer Master claim vocabulary, open), D40 (mapping_rules shape)
- `services/dis-ui/CLAUDE.md`, `README.md`

## Carry-forward

- Sources CRUD remains the one held UI screen, still blocked on the open source-registration and allocation contract.
- Open items pending the Sanjeev coordination message (deferred, to be batched): source identity and registration; the attach-to-existing field on onboarding 2.1; the canonical-column subset confirmation; the notification link target mismatch; the resubmit (4.3) and shadow (2.6 to 2.9) shapes this slice builds on provisionally; the RBAC claim vocabulary (D25); the profile / GET /me call; the absent `contracts/customer-master/`. Plus the demand-list illustrative-id sweep before that message goes out.
- When slice 13 lands dis-ui-server, fixture mode flips to real-call mode and the provisional shapes reconcile against Sanjeev's slices 15 to 17.
