# Slice 6: Audit (dis-audit)

## Depends on

- Slice 1 for the applied schema, including the `audit` schema, the partitioned
  `audit.events` parent, and the daily partitions Slice 1 authored for it, plus
  the two Postgres roles (`ithina_dis_admin`, `ithina_dis_user` as NOSUPERUSER
  NOBYPASSRLS) and the RLS policies already in force. The source of truth for the
  `audit.events` shape and its RLS posture is the live schema in the DIS database
  (`ithina_dis_db`, port 5433), introspected in plan mode, not the
  `schemas/postgres/audit/` DDL files (informational, may differ) and not the
  D14 / architecture §8 field list (a snapshot, likely drifted).
- Slice 3 for `dis-core`: `errors` (single `DisError` root), `ids` (UUIDv7),
  `trace_id`, `timestamps` (UTC-only), structured `logging`, and the inert
  Phase-1 `BqClient` stub. Reused here without disturbance or duplication.
- Slice 4 for `dis-rls` (the async RLS-aware session helper) if, and only if, the
  audit writer needs a tenant-scoped session to write to `audit.events`. Whether
  it does is the load-bearing open question of this slice and turns on the live
  RLS posture of `audit.events` (plan mode). Slice 4 also set the seam discipline
  this slice follows (the `dis-pii` inert placeholder seams, mirroring the Slice 3
  `BqClient` stub) and three lessons that bind here: target-safety on any
  Postgres write, the non-vacuous-test bar, and load-bearing proofs that ERROR
  (never skip) when a dependency is absent.
- Slice 2 for the fixture seeder, the only sanctioned way to put identity rows in
  place, needed by the writer test only if `audit.events` carries an FK to
  `identity_mirror` or the chosen write posture requires a real tenant context.
  Whether the test needs seeded identity is derived in plan mode from the live
  schema, not assumed.
- Zero Slice 5 surface. Slice 5 (`dis-mapping`, `dis-validation`) is in flight on
  a separate workstream; this slice is drafted against the Slice 4 boundary and
  assumes none of Slice 5's surface exists. `mapping_version_id` is a field value
  the caller supplies, not a code dependency on `dis-mapping`. Plan mode flags any
  real Slice 5 dependency it finds rather than silently taking one.
- CLAUDE.md hard rules: audit emission is fire-and-forget (rule 11); no async DB
  call outside an RLS context (rule 12), which is in tension with fire-and-forget
  here and must be reconciled, not silently picked; `trace_id` is propagated, never
  minted mid-pipeline (rule 4); UUIDv7 via the `dis-core` `ids` helper (rule 3);
  errors from `dis-core/errors.py` (no raw `RuntimeError` / `ValueError`); BigQuery
  access via `BqClient` only (rule 8), which bounds the Phase-3 writer stub; never
  log PII or raw payloads.
- No forward dependency blocks this slice. Downstream consumers are the
  csv-ingest-worker (Slice 9), the streaming consumer (Slice 10), the quarantine
  drainer (Slice 11), and the dis-ui-server audit-lookup read path (Slice 13).
  Slice 5 and Slice 10 import the stage and outcome vocabulary this slice owns.
  These consumers size the surface area.

## Goal

After this slice, `libs/dis-audit` exists and is importable. An audit event can be
represented as a model aligned to the live `audit.events` schema, and written to
the Cloud SQL `audit.events` table through a writer whose failures are logged with
context and never raised to or block the caller (hard rule 11). The stage and
outcome vocabulary the pipeline records (the per-stage `stage` values and the
duplicate outcomes of D33) is owned here, so Slice 5 and Slice 10 import it from
`dis-audit` rather than redefining it. The BigQuery archival path is an inert
Phase-3 placeholder seam only, mirroring the Slice 3 `BqClient` and Slice 4
`dis-pii` seam discipline. No service emits audit events in this slice; emission is
service-layer and begins Slice 7 onward. This slice writes to Postgres in its
writer test, so the target-safety discipline applies.

## Task

Build one lib in the directory the repo already reserves for it; confirm exact
placement in plan mode rather than inventing dirs.

`libs/dis-audit`, scoped to four responsibilities:

1. An audit-event model representing one `audit.events` row, hand-aligned to the
   live `ithina_dis_db` schema. Its fields, types, nullability, and any enum
   values are derived by introspection in plan mode, not from D14, architecture
   §8, the DDL files, or the BigQuery `audit_events` shape. A both-directions
   column-set reconciliation against the live schema is the drift guard (the
   Slice 3 `dis-canonical` discipline).
2. A writer that lands audit events in Cloud SQL `audit.events`, fire-and-forget:
   a write failure is logged with `tenant_id`, `trace_id`, and the load-bearing
   identifiers, and is never raised to the caller and never blocks the data path
   (hard rule 11). The write posture (whether it goes through `dis-rls` or under a
   distinct posture) follows from the live RLS posture of `audit.events`, resolved
   in plan mode; reconcile hard rule 11 against hard rule 12 rather than silently
   picking one.
3. A backend-selecting writer seam: the Phase-1 Postgres writer is active; the
   Phase-3 BigQuery writer is an inert placeholder seam (import-safe, no BigQuery
   contact, no method bodies fleshed out), behind `BqClient` per hard rule 8. The
   writer interface stays stable across the Phase-3 addition (D34).
4. The stage and outcome vocabulary, owned by `dis-audit` and imported by its
   consumers. Membership, and whether the duplicate outcomes (DUPLICATE_NOOP,
   DUPLICATE_OVERWRITTEN, and the CANONICAL_WRITTEN family of D33) are a distinct
   field or part of the per-stage status, are derived in plan mode from the live
   `audit.events` columns and what the named consumers set, building to current and
   upcoming need only.

## Acceptance criteria

1. `libs/dis-audit` is importable and depends only on `dis-core` (and, if the
   write posture requires it, `dis-rls`), plus its stack libs, with an acyclic
   import graph. It does not depend on `dis-mapping`, `dis-validation`, or
   `dis-canonical` unless plan mode surfaces a real need and it is recorded here.
   `make check` shows no tier regression and the new tests pass.
2. The audit-event model has one field per live `audit.events` column, each
   load-bearing field derived with introspected evidence (type, nullability, FK,
   enum), reconciled against the live schema both directions (exact set match) as a
   drift guard. No column is asserted from D14, architecture §8, the DDL, the
   BigQuery `audit_events` shape, or any snapshot.
3. The Phase-1 Postgres writer lands an audit event in `audit.events`, verified
   against the live table (the row is present with the expected field values),
   under the write posture chosen in plan mode.
4. Fire-and-forget is proven, not assumed: a test drives the writer against a
   backend made to fail and asserts the failure is logged with context and no
   exception propagates to the caller. This proof ERRORS (never skips) if its
   failing-backend dependency cannot be established, so green means it ran.
5. The BigQuery writer seam is import-safe and makes no BigQuery, network, or DB
   call; its surface is the minimum needed to express backend selection and mark
   the Phase-3 implementation point, behind `BqClient` (hard rule 8). No method
   bodies are fleshed out.
6. The stage and outcome vocabulary is defined in `dis-audit` and importable; its
   membership matches the live `audit.events` columns and the values the named
   consumers set, with nothing built for later slices beyond current need.
7. Every Postgres-touching test runs only against `ithina_dis_db` on 5433 and
   never against Customer Master on 5432, with a guard that makes the wrong target
   impossible, not merely unlikely.
8. The lib raises `dis-core` errors (no raw `RuntimeError` / `ValueError`), binds
   `tenant_id`, `trace_id`, `service`, `stage` in logs where applicable, logs no
   PII or raw payloads, never mints `trace_id` (hard rule 4), and mints any UUIDs
   via the `dis-core` `ids` helper.
9. The lib's `CLAUDE.md` records its new invariants before slice exit (per-lib
   `CLAUDE.md` under 50 lines).

## Scope boundary

In scope:
- `dis-audit`: the audit-event model, the fire-and-forget Phase-1 Postgres writer
  and its non-vacuous test, the backend-selecting writer seam with the inert
  Phase-3 BigQuery placeholder, and the stage and outcome vocabulary it owns.
- The workspace wiring that makes `dis-audit` a member and keeps the tree
  buildable at each step.

Out of scope (do not let the slice sprawl):
- Any service, receiver, worker, or pipeline logic, and any wiring of `dis-audit`
  into a consumer. No service emits audit events here; emission is service-layer
  from Slice 7 onward. The named consumers (Slices 9, 10, 11, 13) size the surface
  but are not built here.
- A real BigQuery writer or any BigQuery contact. That is Phase 3 (Slice 21), per
  D34. The seam is inert.
- Building `dis-audit` surface later slices do not yet need (for example batch or
  bulk-write helpers, an async background-drain mechanism, or stage values no
  current consumer sets) beyond what the named consumers and the writer test
  require. Build to current and upcoming need; later slices extend. Confirm the
  minimal surface in plan mode.
- Resolving the audit RLS-vs-fire-and-forget posture as an architecture change. If
  the live posture and the two hard rules cannot be satisfied together cleanly,
  register the gap with its own D-number and surface it; do not settle it by
  editing DDL or rules in this slice.
- Resolving D37 (external `t_*` / `s_*` IDs vs internal UUID keys; OPEN, deadline
  Slice 7) and D38 (event-table dedup columns; OPEN, deadline Slice 10). Neither
  is this slice's to settle. Audit references identifiers by their internal keys
  and `trace_id`.
- Authoring or changing any DDL. If a needed column, partition, or constraint on
  `audit.events` is missing or wrong, or if the live shape diverges from the D14 /
  architecture §8 list or the BigQuery `audit_events` shape, surface it in plan
  mode and register it; do not edit DDL in this slice.

## Constraints

- The `audit.events` shape and its RLS posture, and any column a check reads, are
  derived from the live `ithina_dis_db` schema on 5433, introspected in plan mode,
  never from the DDL files, D14, architecture §8, the BigQuery `audit_events`
  shape, or any snapshot.
- Load-bearing schema and code claims carry their evidence inline (the introspected
  row, policy, partition, or constraint, or the file and line), not a DDL line or a
  summary.
- This slice writes to Postgres, so the target-safety pass is item 1 of the plan:
  which database and port, what the writing path does, and the guard that refuses
  the wrong target. 5433 / `ithina_dis_db` only; never 5432 / Customer Master.
- Audit emission is fire-and-forget (hard rule 11): a write failure is logged with
  context and never raised to or blocks the caller. This is the one sanctioned
  exception to the no-swallowed-exceptions rule (code-quality rule 6); the swallow
  is explicit, logged with `tenant_id` and `trace_id`, and scoped to the audit
  write path only. Fire-and-forget must not become a blanket excuse for silent
  loss: a missing partition, a missing grant, or a schema mismatch that fails the
  write is logged as an error worth alerting on, not absorbed as routine.
- Hard rule 12 (no async DB call outside an RLS context) and hard rule 11
  (fire-and-forget) both bear on the write posture. Reconcile them against the live
  RLS posture of `audit.events`; surface and register any irreducible tension,
  settle none by fiat.
- Errors inherit from the single `dis-core` `DisError` root; no raw `RuntimeError`
  / `ValueError`. UUIDv7 only via the `dis-core` `ids` helper. `trace_id` is read
  from the caller, never minted (hard rule 4). Structured logging binds
  `tenant_id`, `trace_id`, `service`, `stage`; never log PII or raw payloads, and
  never let a raw payload reach an audit field.
- The Phase-3 BigQuery writer seam is import-safe with no BigQuery, network, or DB
  contact, behind `BqClient` (hard rule 8), mirroring the Slice 3 `BqClient` stub
  and Slice 4 `dis-pii` seam discipline.
- "Green" is a weak signal here. The fire-and-forget criterion is the one most able
  to pass vacuously (a writer that never reaches its backend also never raises); it
  is proven by a test that drives a failing backend and errors if that dependency
  is absent, and register gaps are logged with their own identifiers before commit,
  in the same pass.
- New per-lib invariants are captured in the lib's `CLAUDE.md` before slice exit
  (per-lib `CLAUDE.md` under 50 lines).
- The lib lives in the directory the repo already reserves for it; confirm exact
  placement in plan mode rather than inventing dirs.

## Open questions (for plan mode to resolve)

1. `audit.events` shape and the audit-event model. Introspect the live
   `audit.events` schema in `ithina_dis_db` (5433, not Customer Master on 5432) as
   the derive-from source. Enumerate its columns and derive each field (type,
   nullability, FK, enum) with introspected evidence, including how partitioning is
   expressed and whether `mapping_version_id` and the duplicate-outcome columns are
   present. Reconcile the live shape against the D14 / architecture §8 field list
   and the BigQuery `audit_events` shape both directions; where they diverge,
   register the drift (the D38 analog for audit) and do not edit DDL. Confirm
   whether `audit.events` has a DEFAULT partition and what happens when no daily
   partition covers the write date: with fire-and-forget, a write outside the
   created partition range raises and is then swallowed, so the audit row vanishes
   silently. Name silent audit loss as a risk, not an acceptable swallow.

2. Write posture: `dis-rls` or distinct. Introspect the live RLS posture of
   `audit.events` (`relrowsecurity`, `relforcerowsecurity`, `pg_policies`). Decide
   whether the writer opens a `dis-rls` tenant-scoped session (hard rule 12) or
   writes under a distinct posture, given that audit is fire-and-forget (hard
   rule 11), cross-tenant ops-queryable, and emitted per stage. If the live posture
   and the two rules cannot be satisfied together cleanly, register the tension
   with its own D-number (the D41 analog) and flag it; settle nothing by fiat.
   Two facts feed this: whether `tenant_id` is nullable on `audit.events` (early
   stages emit before identity resolves; a NOT NULL column constrains both the
   model and the posture), and whether the writing role (`ithina_dis_user`,
   NOBYPASSRLS) actually holds INSERT on `audit.events` and its partitions (Slice
   1 grants plus ALTER DEFAULT PRIVILEGES). A missing grant fails the write, which
   fire-and-forget then swallows.

3. Writer API shape and fire-and-forget mechanism. Decide the writer's signature
   (sync or async; single-event or batch; the SQLAlchemy 2.0 async wiring if
   async), and how fire-and-forget is realized (best-effort within the call, a
   background task, or another mechanism) without introducing a silent fallback for
   a required value (code-quality rule 4). State the minimal surface; do not build
   batch or background-drain helpers unless a named consumer needs them. Confirm the
   architecture's "streaming insert (bundled)" framing against what Phase 1 actually
   needs.

4. Stage and outcome vocabulary. Confirm, from the live `audit.events` columns and
   what the named consumers set, the membership of the stage enum and whether the
   duplicate outcomes (DUPLICATE_NOOP, DUPLICATE_OVERWRITTEN, and the
   CANONICAL_WRITTEN family of D33) are a distinct field or part of the per-stage
   status. Include the INGRESS_EVENT vs ROW audit scope distinguisher (glossary)
   and `prior_trace_id` (D33, recorded on duplicates) in what the vocabulary and
   model must carry. Confirm the vocabulary is owned by `dis-audit` with no
   dependency on `dis-mapping` or `dis-canonical`, so Slice 5 and Slice 10 import
   it cleanly.

5. Writer-test design (non-vacuous). Decide what the happy-path test writes and how
   it reads the row back independently, and how the fire-and-forget test drives a
   failing backend so the swallow is exercised (not bypassed) and the proof errors
   if the failing-backend dependency cannot be established. Confirm whether the
   test needs seeded identity rows (from the Slice 2 seeder) because `audit.events`
   carries an FK to `identity_mirror` or because the chosen write posture requires a
   real tenant context, by introspection rather than assumption.

6. Dependency direction and placement. Confirm `dis-audit`'s dependencies
   (`dis-core` only, or also `dis-rls`), that it does not depend on `dis-mapping`,
   `dis-validation`, or `dis-canonical` (or, if a real need surfaces, record it and
   re-confirm the Slice 5 independence the operator pinned), that the import graph
   is acyclic, and the exact reserved directory for the lib. Do not invent dirs.

7. Phase-1 audit-write idempotency. Architecture §6.2 calls audit at-least-once,
   "idempotent in BQ via insertId." Pub/Sub redelivery and the DUPLICATE_NOOP
   reprocess path mean the same (trace_id, stage) can be emitted more than once.
   Decide the Phase-1 Cloud SQL posture: tolerate duplicate rows, dedup on a key,
   or none, derived from the live `audit.events` schema (any unique key or its
   absence) and the named consumers' delivery semantics. If the Phase-1 posture
   diverges from the BQ insertId model, register it rather than settling it here.
