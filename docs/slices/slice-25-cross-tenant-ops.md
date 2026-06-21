# Slice 25: Cross-tenant Quarantine and Audit search (ops modes)

**Status:** TODO.

**Phase:** Phase 3 (ops cluster). The two cross-tenant ops extensions. Builds on slices 20 to 24.

**Owners:** UI track owns the screens and their API shapes; Sanjeev owns the cross-tenant access and cross-tenant action policy (RLS) these depend on.

## Goal

Give ops a cross-tenant view of two screens that already exist for tenants: the Quarantine console and the Audit lookup. These are built as ops MODES of the existing screens (tenant-aware: one component, two modes, branched on isOps), reached at new ops routes under the OpsBoundary guard built in slice 24. The tenant screens and their behavior are NOT changed; the ops mode is purely additive.

- Ops Quarantine (/ops/quarantine): a fleet-wide quarantine list with a tenant column and a tenant filter, and the resubmit action retained (carrying the row's tenant context).
- Ops Audit (/ops/audit): cross-tenant trace search (demand list 5.2) with a tenant column.

Built in the slice 23 design system at the craft bar, light and dark.

## Why tenant-aware (and the risk we are accepting)

The operator chose to make the existing screens tenant-aware (one component serving both tenant-scoped and cross-tenant modes) rather than build separate ops screens. This trades duplication for FM1 risk: the working tenant Quarantine and Audit screens have passing behavioral tests and a tenant who depends on them, and a single component serving two modes can regress the tenant path. We accept this deliberately and guard the tenant path hard (see FM1). The tenant-mode tests are the contract that tenant behavior is unchanged.

## The cross-tenant dependencies (named up front)

Both screens lean on cross-tenant read, the thinnest-specified part of the system (already flagged on slice 24 Fleet). This slice adds a second, sharper dependency: the ops cross-tenant Quarantine retains the resubmit action, which means an ops user triggers a tenant-scoped mutation (with the chain-depth-3 cap) in ANOTHER tenant's context. Whether an ops token is permitted to read across tenants, and whether it is permitted to ACT (resubmit) in a tenant's context, are both Sanjeev's RLS/policy calls and neither is defined.

So: the UI defines the cross-tenant read shapes and the tenant-context resubmit shape; it does NOT invent how either is authorized. Both go to the batched Sanjeev message as first-class open questions. Every cross-tenant shape is a flagged provisional constant in one containment file.

## Scope

In:
- isOps-branched ops mode in the Quarantine console: fleet-wide list, tenant column, tenant filter, resubmit retained (mutation carries the row's tenant_id, not the ops user's null tenant)
- isOps-branched ops mode in the Audit lookup: cross-tenant trace search (5.2), tenant column
- ops routes /ops/quarantine and /ops/audit under the existing OpsBoundary guard, rendering the existing components in ops mode
- the ops nav group gains Quarantine and Audit entries (visible to ops only)

Out (later slices):
- DuckDB Query Panel (7.4 to 7.5) - slice 26
- Sources CRUD - slice 27
- any change to tenant-mode behavior (explicitly forbidden)

## Hard constraints

1. **Tenant mode is the untouched contract (FM1).** When the persona is tenant, both components behave byte-for-byte as they do today. The existing tenant Quarantine and Audit tests stay green with their behavioral assertions INTACT (not adjusted, not relaxed). Any existing tenant test needing an assertion change is a stop-and-surface signal that the modes have entangled.
2. **Ops mode is additive, branched on isOps only.** The tenant branch is the existing code path. The ops branch adds the tenant column, the cross-tenant data source, the tenant filter, and (Quarantine) the tenant-context resubmit. No new auth logic; isOps is the single gate.
3. **Ops routes under the guard.** /ops/quarantine and /ops/audit sit under OpsBoundary, so a non-ops persona is denied (inherits slice 24). The cross-tenant view is unreachable by a tenant even though it is the same component.
4. **Containment.** Cross-tenant shapes (the fleet-wide quarantine query/result, the cross-tenant trace search query/result, the tenant-context resubmit request) are flagged provisional constants in src/lib/dis-ui-server/ops-cross-tenant.ts (or extend the existing fixture files with clearly flagged ops-only constants), naming demand list 5.2 and the quarantine sections. Single reconciliation point.
5. **Cross-tenant resubmit carries tenant context.** The ops resubmit mutation sends the row's tenant_id; the chain-depth-3 cap and the confirm dialog behave as in tenant mode. The authorization of a cross-tenant resubmit is Sanjeev's policy, recorded as open, not invented.
6. **Design system at the craft bar.** Built on the slice 23 primitives; the tenant column and tenant filter use the same Table/Select/Badge treatment. Light and dark.
7. **No backend, no contract edits, no canonical changes.** Pure UI on fixtures. Tokens unchanged.
8. **Repo hygiene + git discipline.** No em-dashes; precise DIS / DIS UI / dis-ui-server; strict TS no any; ESLint/Prettier clean. Claude Code does not push; operator reviews and pushes; no Co-Authored-By; one coherent commit.

## Open product questions (flag, do not invent)

- **Audit ops search semantics:** tenant Audit is lookup-by-trace_id. Is ops Audit (5.2) still lookup-by-trace (just cross-tenant, with a tenant column on the result), or genuine search across tenants by other criteria (source, stage, time)? Default for this slice: cross-tenant lookup-by-trace with a tenant column, since 5.2's richer search shape is underspecified. Flag the richer-search seam; do not build it.
- **Cross-tenant resubmit authorization:** retained per the operator's decision, carrying tenant context. Whether ops may act in a tenant's context is Sanjeev's policy. Flagged open.
- **Fleet-wide quarantine volume:** a real fleet-wide list could be large; this slice uses a bounded fixture and notes pagination as a seam, not built.

## Acceptance criteria

1. In tenant mode, the Quarantine console and Audit lookup behave exactly as before (existing tenant tests green, assertions intact).
2. /ops/quarantine renders for ops: a fleet-wide quarantine list with a tenant column and a working tenant filter; rows show stage as a Badge, mono trace with copy (as today).
3. Ops Quarantine retains resubmit: the confirm dialog opens, the mutation carries the row's tenant_id, the chain-depth-3 cap is enforced, behavior otherwise matches tenant-mode resubmit.
4. /ops/audit renders for ops: cross-tenant trace lookup with a tenant column on the lifecycle result.
5. Both ops routes are under OpsBoundary; a non-ops persona is denied (inherits slice 24).
6. The ops nav group shows Quarantine and Audit for ops, hidden for tenant.
7. All cross-tenant shapes (queries, results, the tenant-context resubmit request) are flagged provisional constants in one containment file; the screens consume typed values only.
8. Craft-bar look in light and dark; shared state components for empty/loading/error.
9. pnpm install, dev (200, no console errors), build, test, lint, tsc --noEmit strict all green; the full prior suite green, tenant-mode assertions unchanged, ops-mode tests additive.
10. No em-dashes; correct naming; grounded ids (external strings, D37 open); tokens unchanged.

## Failure modes

- **FM1: Tenant-mode regression (the headline).** The tenant path must be byte-for-byte unchanged. The existing tenant Quarantine/Audit tests are the contract and stay green with assertions intact. If any tenant-mode assertion would change, STOP and surface; the modes have entangled.
- **FM2: Shape leakage.** Cross-tenant shapes stay flagged constants in the containment file, not threaded into the components. Single reconciliation point.
- **FM3: Inventing cross-tenant policy.** The UI defines the read and resubmit shapes; it does NOT invent how cross-tenant read or cross-tenant resubmit is authorized. Both are Sanjeev's RLS/policy, recorded open. Surface what 5.2 and the fleet-quarantine shape leave underspecified rather than inventing it.
- **FM4: Mode bleed.** Tenant-mode rendering must not gain a tenant column or cross-tenant data; ops-mode must not lose the tenant scoping when acting (resubmit carries the row tenant). The isOps branch is the only switch.
- **FM5: Scope creep.** No DuckDB, no Sources CRUD, no richer 5.2 search, no pagination this slice. Note seams.

## Plan-mode prompt (single checkpoint)

> "Read docs/skills/sevyn8-workflow/SKILL.md, this slice doc, services/dis-ui/docs/dis-ui-visual-craft-spec.md, services/dis-ui/docs/dis-ui-surface-map.md (Quarantine, Audit, and the ops cluster / cross-tenant 5.2), docs/ui-engineer-demand-list.md 4.x (quarantine + resubmit 4.3) and 5.2, services/dis-ui/CLAUDE.md, and your current code: the Quarantine console (QuarantineConsole.tsx + quarantine.ts + tests, including the resubmit + chain-depth logic), the Audit lookup (AuditLookup.tsx + audit.ts + tests), src/auth (isOps, useAuth), the OpsBoundary guard and the ops routing/nav from slice 24, and the slice 23 primitives.
>
> Produce a plan to:
> 1. add the cross-tenant shapes as flagged provisional constants in a single containment file (fleet-wide quarantine query/result, cross-tenant trace lookup result with tenant, the tenant-context resubmit request), naming 4.3 and 5.2; bounded multi-tenant fixtures using grounded external-string tenant ids
> 2. make the Quarantine console tenant-aware: an isOps branch that, in ops mode, sources the fleet-wide list, adds a tenant column and a tenant filter, and routes resubmit through the tenant-context mutation (carrying the row tenant_id); tenant mode is the existing path UNCHANGED
> 3. make the Audit lookup tenant-aware: an isOps branch that, in ops mode, does cross-tenant trace lookup and adds a tenant column to the lifecycle result; tenant mode unchanged
> 4. register /ops/quarantine and /ops/audit under OpsBoundary, rendering the existing components in ops mode; add the ops nav entries (ops-only)
> 5. tests: tenant-mode Quarantine/Audit tests stay green with assertions INTACT (FM1); new ops-mode tests (fleet list + tenant column + filter; ops resubmit carries tenant_id and respects depth-3; cross-tenant audit lookup with tenant column; non-ops denied on both ops routes; nav visibility)
>
> Return the file list, the cross-tenant shapes and where they live, the isOps-branch approach per screen, the route/nav additions, and the test list (explicitly: confirm NO existing tenant-mode assertion changes; list ops-mode tests as additive). FM1 is the headline: tenant mode byte-for-byte unchanged. FM3: surface what 5.2 and the fleet-quarantine shape leave underspecified; do NOT invent cross-tenant read or resubmit authorization. Return the plan and STOP."

## After approval

Execute, then verify: install / dev / build / test / lint / tsc strict all green with the count; an acceptance-criteria table for criteria 1 to 10; explicitly confirm (a) tenant-mode Quarantine and Audit behavior unchanged with assertions intact, (b) ops mode renders cross-tenant with tenant column/filter, (c) ops resubmit carries tenant_id and respects depth-3, (d) non-ops denied on both ops routes. One commit, subject "services/dis-ui: Slice 25 cross-tenant Quarantine and Audit (ops modes)", no Co-Authored-By. Do not push; show the diff summary and hash and stop.

## Carry-forward

- Two first-class open items for the batched Sanjeev message: cross-tenant ops READ (shared with slice 24 Fleet) and cross-tenant ops ACTION (the tenant-context resubmit authorization, new here).
- Remaining ops cluster after this: DuckDB Query Panel (7.4 to 7.5) - slice 26.
- Remaining tenant work: Sources CRUD - slice 27.
