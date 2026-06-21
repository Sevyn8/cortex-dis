# Slice 3: Core primitives (dis-core) and canonical models (dis-canonical)

## Depends on

- Slice 1 for the applied schema. The source of truth is the live schema in the
  DIS database (`ithina_dis_db`, port 5433), not the DDL files. Slice 1 applied
  the DDL, but the live schema is what `dis-canonical` models derive from, by
  introspection in plan mode. The `schemas/postgres/` DDL files are informational
  reference, not authoritative, and may differ from what is applied.
- Slice 2 for the interim exceptions to consolidate (three in
  `dis_core/identity/client.py`, three in `dis_testing/errors.py`) and the
  existing `dis-core/identity/` package, which must not be disturbed or
  duplicated.
- CLAUDE.md hard rules: UUIDv7 everywhere (never `uuid4`); error types defined
  in `dis-core/errors.py` (no raw `RuntimeError`/`ValueError`); structured
  logging carrying `tenant_id`, `trace_id`, `service`, `stage`.
- No forward dependency blocks this slice. Downstream consumers are Slices 4 to
  6 (`dis-rls`, `dis-pii`, `dis-storage`, `dis-mapping`, `dis-validation`,
  `dis-audit`) and Slice 10 (streaming consumer); they size the surface area.

## Goal

After this slice, the foundational primitives every later lib and service
imports exist in `libs/dis-core` (IDs, trace_id, timestamps, structured logging,
a single error hierarchy, a Phase-1 `BqClient` stub), and `libs/dis-canonical`
provides Pydantic models for the canonical schemas aligned to the live
`ithina_dis_db` schema. The
six interim exceptions Slice 2 left local are consolidated into the real
`dis-core` hierarchy, and the identity client re-uses the shared errors without
`dis-core` depending on `dis-testing`. No service, receiver, or pipeline logic
is built; this slice is libs only, with no Postgres writes.

## Task

Build two libs.

1. `libs/dis-core` additions, placed alongside the existing `identity/` package
   without disturbing or duplicating it:
   - `ids`: the canonical UUIDv7 generation helper (single home for UUIDv7).
   - `trace_id`: generation and context-local access.
   - `timestamps`: UTC-only helpers (never naive datetimes).
   - `logging`: the structured-logging convention binding `tenant_id`,
     `trace_id`, `service`, `stage`.
   - `errors`: the real exception hierarchy under a single `DisError` root,
     consolidating the six interim exceptions.
   - `BqClient`: a Phase-1 stub only (no BigQuery contact); a minimal seam the
     real Phase-3 client replaces.
2. `libs/dis-canonical`: Pydantic models for the canonical schemas, derived in
   plan mode by introspecting the live `ithina_dis_db` schema, reflecting the
   post-D36 store keying.
3. Consolidation and adoption: move the six interim exceptions into
   `dis-core/errors.py`; have the identity client re-use the shared errors
   without `dis-core` depending on `dis-testing`; decide whether the three
   `dis-testing` infra errors become `dis-core` errors or stay test-only
   subclasses of a shared root.

## Acceptance criteria

1. `libs/dis-core` exposes `ids` (UUIDv7), `trace_id`, `timestamps`, `logging`,
   `errors`, and a `BqClient` stub; all importable. `make check` shows no tier
   regression and the new unit tests pass.
2. The error hierarchy has a single `DisError` root; the six interim exceptions
   (three from `identity/client.py`, three from `dis_testing/errors.py`) are
   consolidated per the plan-mode decision, with no duplicate definitions
   remaining anywhere.
3. `dis-core` does not import `dis-testing` (no test-to-core inversion); the
   import graph is acyclic, and `errors.py` is leaf-level within `dis-core` (it
   does not import `identity`).
4. The existing identity client behaves as before. If it adopts the shared
   errors, its existing tests still pass (no regression).
5. UUIDv7 is generated only via the `dis-core` `ids` helper; no `uuid4`
   anywhere. Whether Slice 2's direct `uuid-utils` usage in fixtures is
   retrofitted or left as-is is decided in plan mode and reflected here.
6. `libs/dis-canonical` provides a Pydantic model per canonical table, each
   field derived from the live `ithina_dis_db` schema with introspected evidence
   for load-bearing fields (type, nullability, FK, enum), reflecting the post-D36
   store keying.
7. A `dis-canonical` model round-trips: an instance validates and serializes;
   field types match the DDL (UUID, Decimal, timestamptz, enums) per the inline
   evidence.
8. The `BqClient` stub is import-safe and makes no network or BigQuery call; its
   surface is the minimum Phase-1 consumers need (none today), documented as the
   Phase-3 replacement seam.

## Scope boundary

In scope:
- `dis-core`: `ids`, `trace_id`, `timestamps`, `logging`, `errors`, `BqClient`
  stub.
- `dis-canonical`: Pydantic models for the canonical schemas, aligned to the
  live `ithina_dis_db` schema.
- Consolidating the six interim exceptions into `dis-core/errors.py`.
- Letting the identity client adopt the shared errors (if chosen in plan mode)
  without regression.

Out of scope (do not let the slice sprawl):
- Any service, receiver, pipeline, or runtime logic.
- A real `BqClient` or any BigQuery contact. That is Phase 3 (Slice 21).
- `dis-rls`, `dis-pii`, `dis-storage`, `dis-mapping`, `dis-validation`,
  `dis-audit` (Slices 4 to 6). Do not build their surface here.
- Building `dis-core` surface area later slices do not yet need. Build to
  current and upcoming need; later slices extend.
- SQLAlchemy or ORM models for canonical. The consumer's DB layer owns SQL
  conversion; `dis-canonical` is the in-memory Pydantic representation only.
- Resolving D37 (external `t_*`/`s_*` IDs vs internal UUID keys; OPEN, deadline
  Slice 7). Slices 3 to 6 do not need it.
- Authoring or changing any DDL. If a canonical DDL file is wrong or missing,
  surface it in plan mode; do not edit it in this slice.

## Constraints

- `dis-canonical` models derive from the live `ithina_dis_db` schema,
  introspected in plan mode (`\d+` / `information_schema`), never from the DDL
  files or any snapshot. The DDL is informational; the applied schema is
  authoritative and may carry changes that rode in via commits, or partitioning
  the DDL text does not reflect. Architecture, decisions, the handoff context,
  and the older libs-context doc all lag and are not sources.
- Load-bearing schema claims carry the introspected evidence inline (the
  schema-qualified `\d+` row or `information_schema` result for that column, FK,
  or constraint), not a DDL line or a summary. The Slice 1 audit `event_date`
  discipline applies to every load-bearing field; here the evidence is the live
  DB, not a file.
- All UUIDv7 generation goes through the `dis-core` `ids` helper; never
  `uuid.uuid4` (CLAUDE.md hard rule 3).
- `dis-core` never depends on `dis-testing`. `errors.py` is leaf-level (no
  `identity` import). The final import graph is acyclic.
- Errors inherit from a single `DisError` root (CLAUDE.md: define error types in
  `dis-core/errors.py`; no raw `RuntimeError`/`ValueError`).
- Structured logging binds `tenant_id`, `trace_id`, `service`, `stage`; never
  log PII or raw payloads (CLAUDE.md logging rule).
- `BqClient` is a Phase-1 stub: import-safe, no network, minimal seam, no method
  bodies fleshed out.
- New per-lib invariants are captured in the relevant `CLAUDE.md` before slice
  exit (per-lib `CLAUDE.md` under 50 lines).
- Libs live in the directories the repo already reserves for them; confirm exact
  placement in plan mode rather than inventing dirs.
- Two libs in one slice: keep the acceptance criteria separable, and confirm
  whether `dis-canonical` depends on `dis-core` (likely, for a UUIDv7 field type
  or shared base model).

## Open questions (for plan mode to resolve)

1. Error taxonomy and consolidation. Read the six interim exceptions: three in
   `dis_core/identity/client.py` (`IdentityClientError`, `IdentityNotFoundError`,
   `IdentityServiceUnavailableError`) and three in `dis_testing/errors.py`
   (`TestInfraError`, `FixtureError`, `SeedError`). Decide the `DisError`-rooted
   hierarchy: which become real `dis-core` domain errors, which stay test-only
   in `dis-testing` as subclasses of a shared `dis-core` root, the base-class
   shape, and confirm the resulting import graph is acyclic with `errors.py`
   leaf-level. Derive the intended set from what Slice 4 to 6 consumers will
   raise, not only from what exists today.

2. Identity client adoption scope. Decide explicitly: define the shared errors
   only, or define and retrofit `identity/client.py` to use them. If adopting,
   the existing identity client tests must still pass (a no-regression
   criterion). State which, so the diff is bounded and Slice 2 code is not
   silently churned.

3. UUIDv7 helper and Slice 2 retrofit. Confirm the single `ids`-helper approach
   (library choice, client-side UUIDv7 for `trace_id` at request start,
   alignment with the Postgres `public.uuidv7()` function from Slice 1). Decide
   whether Slice 2's direct `uuid-utils` usage in fixtures is retrofitted to the
   helper now or left as-is, applying the same define-only-versus-adopt logic as
   the errors.

4. dis-canonical derivation source and coverage. Introspect the live canonical
   schema in `ithina_dis_db` (port 5433, not Customer Master on 5432) as the
   derive-from source; the `schemas/postgres/canonical/` DDL files are
   informational only and the live schema may differ from them. Enumerate which
   canonical tables get models and derive each field (type, nullability, FK,
   enum) with the introspected evidence. Confirm how the post-D36 store keying
   (composite store FK, global `store_id` uniqueness) is represented in the live
   schema, and whether store-keying has its own decision entry or rode in under
   the D36 commit. Do not assert the table count or any column from memory, from
   the DDL, or from any snapshot.

5. dis-canonical build approach: generated versus hand-aligned. The build-guide
   says models are "generated/aligned with the SQL DDL." Decide whether models
   are codegen'd from the live schema (introspection-driven) or hand-written and
   aligned to it, weighing the cost of real schema-to-Pydantic codegen against
   hand-maintenance drift. Pick one. If codegen, confirm where the generator
   lives (`tools/codegen` per repo layout) and whether it is in scope here or
   deferred.

6. Shared canonical fields and `mapping_version_id`. Every canonical row carries
   `mapping_version_id` (D22 pinning). Confirm how that and other shared columns
   (`tenant_id`, `store_id`, `trace_id`, `event_ts`/`received_ts`) are typed in
   the models, and whether a shared base model or identifiers module is
   warranted, without over-building for later slices.

7. BqClient stub surface. Confirm the minimal Phase-1 stub surface (import-safe,
   no BigQuery contact) and that no current consumer needs more. Define the seam
   the real Phase-3 `BqClient` (Slice 21) replaces, without writing method
   bodies.

8. dis-core module placement and no-disturbance. Confirm the exact module layout
   for the new `dis-core` additions alongside the existing `identity/` package,
   that nothing in `identity/` is duplicated or moved except the consolidated
   errors, and confirm the live `dis-core` layout in plan mode (snapshot docs
   disagree on file names: `ids.py` versus `types.py`, presence of `result.py`
   and `models.py`).
