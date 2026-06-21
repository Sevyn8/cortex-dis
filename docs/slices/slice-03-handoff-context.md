# DIS build — handoff context for drafting Slice 3

This file lets a new chat continue the DIS slice build without re-explaining
anything. It is a continuation of the chat that drafted and shipped Slices 1
and 2. Read it first, then follow the kickoff prompt.

## What DIS is

DIS (Data Integration System) is a multi-tenant retail data ETL platform on GCP.
Flow: ingress channels to bronze, streaming consumer to canonical Postgres
(Cloud SQL), BigQuery archive in Phase 3. Beta scale: 5 tenants, 25 stores,
5000 SKUs, ~150K events/day. Built by Sanjeev (operator and architect) and Amit
(engineer) using Claude Code.

## The workflow (the pilot)

The goal of this pilot is to let Claude Code do all implementation from a
goal-level slice doc, with no detailed implementation prompt. Claude AI (this
chat) helps draft and tighten the slice doc, reviews the plan Claude Code
returns, and reviews diffs, but does not write implementation detail.

The loop per slice:
1. Draft the slice doc in chat: Goal, Task, Acceptance criteria, Scope boundary,
   Constraints, Open questions, plus a Depends-on line at top.
2. Save to `docs/slices/slice-NN-<short-name>.md` and commit.
3. Hand the slice doc plus the standing execution prompt to Claude Code in plan
   mode (Shift+Tab twice). Plan mode is research and analysis only, no writes.
4. Claude Code returns a plan. Bring it back to chat.
5. Review the plan together. Execute, or revise and re-plan until right.
6. Execute (manual edit approval while still calibrating the loop). Review diffs.
7. Acceptance met, commit, mark slice DONE in build-guide.md.

The standing execution prompt lives at `docs/slices/_execution-prompt.md`. Its
plan-output contract requires, in order: (1) destructive-action and target
safety, (2) approach, (3) problems and risks surfaced, (4) open questions
resolved, (5) implementation steps, (6) test plan mapping each acceptance
criterion to a verification. Fill `{NN}` and `{name}` per slice.

## Where we are

- Phase 0 complete. Slices 1 and 2 DONE, committed, and pushed to origin/main.
- Slice 1: bootstrap Alembic migration (7 schemas, RLS, partitions, grants,
  audit partitioned on event_date, env-portable target with a DB guard).
- Slice 2: Identity Service fake, Customer Master fake, test fixture seeder.
  Built `libs/dis-core/identity/` (IdentityClient Protocol, Pydantic models,
  HttpIdentityClient) and `libs/dis-testing/` (fakes, seeder, fixtures,
  jwt_verify, plugin). make check is now 60/60 (was 57; +1 from tests now
  existing, +2 from the two fakes).
- A second workstream (Amit) is building Slice 19 (DIS UI) in parallel on the
  same `main`. Two workstreams now land on main, so fetch before push. The D36
  commit (`84b67eb`) also carried a store-keying refactor (composite store FK,
  global store_id uniqueness).

## Hard-won conventions from Slices 1 and 2

- Project-knowledge files (architecture, decisions, build-guide, CLAUDE.md, the
  schema DDL) are a periodic snapshot and ALWAYS lag the live repo. Do not draft
  schema-dependent detail from them. Keep the slice doc at goal level and make
  "read the live DDL and derive from it" an explicit plan-mode task. Claude Code
  reads the live files; chat does not assert schema specifics.
- Evidence-inline discipline: when a plan asserts a load-bearing schema fact
  (a column, a trigger, an index), require the file and line, not a summary.
  Slice 1's audit `event_date` slip and Slice 2's trigger confirmations both
  came from this.
- Target safety is not surfaced unprompted; it is now item 1 of the plan
  contract. Any slice that writes to Postgres confirms it targets the DIS
  database (5433 / ithina_dis_db) and cannot reach Customer Master
  (5432 / ithina_platform_db).
- Scope boundary matters most where a slice could sprawl. For libs, the risk is
  building surface area later slices do not yet need; the build-guide says build
  to current and upcoming need, later slices may extend.
- Architecture decisions are recorded, not silently worked around. Slice 2's
  external-ID gap became `decisions.md` D37 (OPEN), not a buried fixture hack.

## Slice 3 scope (from build-guide.md, confirm live)

Slice 3: Core primitives.
- `libs/dis-core`: UUIDv7 helper, trace_id helper, structured logging, error
  type hierarchy, BqClient stub for Phase 1 (real BqClient in Phase 3).
- `libs/dis-canonical`: Pydantic models for the canonical schemas, aligned with
  the SQL DDL.

## Slice 3 carry-ins (must be folded in)

1. Six-exception consolidation (Slice 2 residual). Slice 3 authors the real
   `dis-core/errors.py`. It must consolidate the interim exceptions Slice 2 left
   local: three in `dis_core/identity/client.py` (IdentityClientError,
   IdentityNotFoundError, IdentityServiceUnavailableError) and three in
   `dis_testing/errors.py` (TestInfraError, FixtureError, SeedError). Decide
   which belong in the real hierarchy and how the identity client re-uses them
   without dis-core depending on dis-testing.
2. dis-core already exists with `identity/` only (Slice 2). Slice 3 adds ids,
   errors, logging, timestamps/trace_id, BqClient stub WITHOUT disturbing or
   duplicating the identity client, and ideally lets the identity client adopt
   the new shared errors.
3. UUIDv7: Slice 2 used `uuid-utils` directly in fixtures. Slice 3's `ids` helper
   is the canonical home; confirm one approach (CLAUDE.md hard rule: UUIDv7
   everywhere, never uuid4).
4. dis-canonical models mirror the LIVE canonical DDL, which includes the D36
   store-keying refactor (composite store FK, global store_id uniqueness).
   Models must be derived from the on-disk DDL in plan mode, not from any
   snapshot. Confirm in plan mode whether the store-keying change has its own
   decision entry or rode in under the D36 commit.

## Deferred (do not resolve in Slice 3)

- D37 (external `t_*`/`s_*` IDs vs internal UUID keys) is OPEN, deadline Slice 7.
  Slices 3 to 6 do not need it. It resurfaces when Slice 7 is drafted.

## Operator preferences (sticky)

- Formatting: no em-dash in output docs (use commas, parens, colons, en-dash ok).
  Narrow margins for rendered docs. Questions one at a time, numbered Q1/X.
- Response shape: brainstorm mode for questions, do mode for imperatives. Lead
  with the load-bearing point; offer expansions as plain bullets; do not expand
  until asked.
- End any next-actions section titled "Next actions" with numbered items.
- One approach not two when not consequential. Brevity over completeness early.
  Push back when warranted. Build-guide is source of truth for state and order.
