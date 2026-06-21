# Slice 4: Data plane safety (dis-rls, dis-pii, dis-storage)

## Depends on

- Slice 1 for the applied schema, the two Postgres roles (`ithina_dis_admin`,
  `ithina_dis_user` as NOSUPERUSER NOBYPASSRLS), and the RLS policies already in
  force. The source of truth for RLS posture is the live schema in the DIS
  database (`ithina_dis_db`, port 5433), not the DDL files. Which schemas carry
  RLS is derived by introspection in plan mode; the `schemas/postgres/` DDL is
  informational and may differ.
- Slice 1's RLS smoke test as the baseline behaviour `dis-rls` must hold:
  connecting as `ithina_dis_user` without a tenant context returns zero rows from
  an RLS-protected table.
- Slice 2 for the test fixture seeder, the only sanctioned way to put identity
  rows (`identity_mirror.tenants` / `identity_mirror.stores`) in place so the
  isolation test has real tenants and FK targets. The seeder is test-only and
  must not become a runtime path.
- Slice 3 for `dis-core` (`errors` with the single `DisError` root, `ids` for
  UUIDv7, `timestamps`, structured `logging`) and `dis-canonical` models, reused
  here without disturbance or duplication. Confirm in plan mode whether the
  isolation test reads canonical rows through `dis-canonical` models or raw.
- CLAUDE.md hard rules: tenant isolation via `dis-rls` (rule 1); no async DB call
  outside an RLS context (rule 12); PII tokenization before any persistence
  (rule 2); GCS access only via `dis-storage` on the frozen canonical path
  (rule 9); UUIDv7 via `dis-core` (rule 3); errors from `dis-core/errors.py`; no
  PII or raw payloads in logs.
- No forward dependency blocks this slice. Downstream consumers are the
  Mirror Sync DB-pull (Slice 7), the CSV ingest worker (Slice 9), the streaming
  consumer (Slice 10), the quarantine drainer (Slice 11), daily compute
  (Slice 18), and dis-ui-server reads; they size the surface area.

## Goal

After this slice, the three data-plane safety libs exist and are importable. Any
canonical Postgres access can be opened through `libs/dis-rls`'s tenant-scoped
session so one tenant cannot read another's rows. Any GCS access can go through
`libs/dis-storage` on the canonical path scheme, including signed-URL issuance.
And `libs/dis-pii` can detect PII-flagged columns and refuse loudly when one is
flagged but no backend is configured to handle it, so PII cannot land silently.
Real PII tokenization, encryption, decryption, key handling, and any token or
ciphertext storage are not built in this slice; they exist as inert placeholder
seams only. No service, receiver, worker, or pipeline logic is built. Unlike
Slice 3, this slice writes to Postgres in its isolation test, so the target-safety
discipline applies.

## Task

Build three libs in the directories the repo already reserves for them; confirm
exact placement in plan mode rather than inventing dirs.

1. `libs/dis-rls`: an async, RLS-aware Postgres session helper. It opens a
   transaction, establishes the per-tenant scope (`app.tenant_id`), runs the
   caller's statements under that scope, and commits or rolls back. It connects
   as a NOSUPERUSER NOBYPASSRLS role, since RLS is silently void for any
   BYPASSRLS role; the helper must make that role posture explicit, not assumed.
   The exact API shape and the SQLAlchemy 2.0 async wiring are derived in plan
   mode. Build the minimal session surface only; no batched-by-tenant wrapper and
   no standalone enforcement assertions unless a current consumer needs them.
2. `libs/dis-pii`, scoped to two responsibilities only, and operating as pure
   functions over a mapping object handed in by the caller (it performs no DB
   access of its own, so it does not depend on `dis-rls`):
   - PII detection: identify which columns the given source mapping flags as PII
     (field-name and pattern based).
   - A fail-loud gate: when a flagged PII column has no configured backend to
     handle it, raise a `DisError`-rooted error before any persistence path can
     run, so accidental PII landing fails loudly rather than silently. In v1.0 no
     real backend exists, so the gate raises on every flagged PII column. The
     not-raise branch is reachable only by an explicitly injected placeholder
     (in tests); a config default or flag that disables the gate is forbidden (a
     silent fallback, hard rule 2 and code-quality rule 4).
   The tokenizer, the encrypt/decrypt path, the per-tenant key handling, and any
   token or ciphertext backend are placeholder seams only: import-safe, no real
   crypto, no network, no DB. They mark where the real implementation lands later
   (mirroring the Slice 3 `BqClient` stub discipline).
3. `libs/dis-storage`: the canonical GCS object-path scheme, signed-URL issuance,
   and GCS object access through a single client wrapper that honours the local
   emulator. The path builder receives `trace_id` and the identity inputs from the
   caller; it never mints `trace_id` (hard rule 4). The path scheme is the frozen
   one (CLAUDE.md hard rule 9); confirm in plan mode against the live convention
   and any contract, not from memory.

## Acceptance criteria

1. `libs/dis-rls`, `libs/dis-pii`, `libs/dis-storage` are importable and depend
   only on `dis-core` (plus their stack libs), with an acyclic import graph.
   `dis-pii` does not depend on `dis-rls` or do any DB access. `make check` shows
   no tier regression and the new tests pass.
2. An isolation test, verified independently (not only by the lib agreeing with
   its own helper), proves that a session scoped to tenant A cannot read tenant
   B's rows in an RLS-protected table, and that a session opened with no tenant
   context reads zero rows (consistent with the Slice 1 smoke test). The session
   helper connects as a NOSUPERUSER NOBYPASSRLS role, asserted by the test, so
   isolation is not silently void. The test must not pass vacuously: confirm in
   plan mode that it would fail if the scope were not set.
3. Every Postgres-touching test runs only against `ithina_dis_db` on 5433 and
   never against Customer Master on 5432, with a guard that makes the wrong
   target impossible, not merely unlikely.
4. `libs/dis-pii` exposes PII detection and a fail-loud gate operating on a
   caller-supplied mapping (no DB access). In v1.0, with no real backend, a
   mapping flagging any PII column raises a `DisError`-rooted error at the gate
   before any persistence step. The not-raise branch is reachable only via an
   explicitly injected placeholder backend (in tests); no config default or flag
   disables the gate.
5. The `dis-pii` crypto, key, and storage seams are import-safe and make no real
   crypto, network, or DB call. Their surface is the minimum needed to express
   the gate and to mark the later implementation point; no method bodies are
   fleshed out.
6. `libs/dis-storage` is verified in two separable parts: (a) it issues a
   well-formed signed URL with the correct expiry, unit-tested offline (signing
   is deterministic and needs no round-trip); (b) it reads and writes GCS objects
   through one emulator-honouring wrapper. The PUT/GET round-trip *through* a
   signed URL is marked emulator-dependent: if the emulator cannot honour it, that
   sub-check is explicitly deferred to real GCS and named as such, while (a) and
   (b) still hold. The canonical path is built on the frozen scheme.
7. All three libs raise `dis-core` errors (no raw `RuntimeError` / `ValueError`),
   bind `tenant_id`, `trace_id`, `service`, `stage` in logs where applicable, log
   no PII or raw payloads, and mint any UUIDs via the `dis-core` `ids` helper.
8. Each lib's `CLAUDE.md` records its new invariants before slice exit (per-lib
   `CLAUDE.md` under 50 lines).

## Scope boundary

In scope:
- `dis-rls`: the async tenant-scoped session helper and its isolation test. This
  is the load-bearing lib of the slice. The test writes to whichever
  RLS-protected table proves isolation with the least seeding burden (plan mode
  chooses; `bronze.*` is cheaper than `canonical.*`, which carries the tenant FK,
  the composite `(tenant_id, store_id)` store FK (D39), `mapping_version_id NOT
  NULL` (D22), and other NOT NULL / CHECK constraints, and so also needs a seeded
  mapping row, not just identity rows). If canonical is chosen, justify it as more
  representative of the streaming-consumer consumer and name the mapping-row
  dependency.
- `dis-pii`: PII detection and the fail-loud gate over a caller-supplied mapping,
  plus inert placeholder seams for tokenize, encrypt, decrypt, key handling, and
  token/ciphertext storage.
- `dis-storage`: canonical path scheme, signed-URL issuance, GCS object access
  via one emulator-honouring wrapper.

Out of scope (do not let the slice sprawl):
- Real PII tokenization, encryption, decryption, per-tenant key handling, or any
  token or ciphertext backend. *Deferred. Trigger: the first receiver that
  carries PII (a non-CSV receiver, or a CSV source mapping that flags a PII
  column).*
- Resolving the long-term PII posture (one-way tokenization versus reversible
  encryption, and what "configured backend" ultimately means). *Deferred. Trigger:
  the recoverability intent is decided, or the first PII-carrying receiver, per
  the register entry this slice opens.*
- Real cloud KMS or cloud key management. *Deferred. Trigger: the first slice that
  provisions cloud infrastructure.*
- Real cloud GCS. Tests use the local emulator only.
- Any service, receiver, worker, or pipeline logic, and any wiring of these libs
  into a consumer. Slices 7 to 18 consume them.
- Building lib surface later slices do not yet need (for example a batched-by-
  tenant transaction wrapper or standalone enforcement assertions in `dis-rls`)
  beyond what the current consumers and the isolation test require. Build to
  current and upcoming need; later slices extend. Confirm the minimal surface in
  plan mode.
- Resolving D37 (external `t_*` / `s_*` IDs versus internal UUID keys; OPEN,
  deadline Slice 7) and D38 (event-table dedup columns; OPEN, deadline Slice 10).
  Neither is this slice's to settle; the canonical path scheme uses internal
  identifiers and `trace_id`, so D37 is not needed here.
- Authoring or changing any DDL. If a needed column, policy, or table is missing
  or wrong, surface it in plan mode and register it; do not edit DDL in this
  slice.

## Constraints

- RLS posture, and any column a check reads (for example whatever a source
  mapping uses to flag PII), are derived from the live `ithina_dis_db` schema on
  5433, introspected in plan mode, never from the DDL files or any snapshot.
  Specifically confirm the live RLS posture of `config.source_mappings`, which
  Slice 1 left as an open question.
- Load-bearing schema and code claims carry their evidence inline (the
  introspected row, policy, or constraint, or the file and line), not a DDL line
  or a summary.
- This slice touches Postgres, so the target-safety pass is item 1 of the plan:
  which database and port, what the writing path does, and the guard that refuses
  the wrong target. 5433 / `ithina_dis_db` only; never 5432 / Customer Master.
- All async DB access goes inside the `dis-rls` session context (hard rule 12);
  never call SQLAlchemy directly against canonical schemas (hard rule 1).
- GCS access goes only through `dis-storage` on the canonical path scheme
  (hard rule 9); never improvise a path.
- The PII gate raises loudly when a flagged column has no configured backend,
  before any persistence; in v1.0 no real backend exists, so it raises on every
  flagged PII column. It never silently passes PII through, and no config default
  or flag may disable it (hard rule 2 intent, code-quality rule 4). No raw PII in
  logs or errors.
- `dis-rls`'s session connects as a NOSUPERUSER NOBYPASSRLS role; a BYPASSRLS
  connection makes RLS silently void, so the role posture is explicit and asserted
  by the isolation test, not assumed. `dis-storage` receives `trace_id` from the
  caller and never mints it (hard rule 4).
- Errors inherit from the single `dis-core` `DisError` root; no raw
  `RuntimeError` / `ValueError`. UUIDv7 only via the `dis-core` `ids` helper.
  Structured logging binds `tenant_id`, `trace_id`, `service`, `stage`; never log
  PII or raw payloads.
- The `dis-pii` placeholder seams are import-safe with no network, DB, or crypto,
  mirroring the Slice 3 `BqClient` stub discipline.
- "Green" is a weak signal here. The isolation criterion is the one most able to
  pass vacuously; it is verified by an independent check, and register gaps are
  logged with their own identifiers before commit, in the same pass.
- New per-lib invariants are captured in each lib's `CLAUDE.md` before slice exit.
- Three libs in one slice: keep the acceptance criteria separable per lib, and
  confirm in plan mode whether each depends on `dis-core` only or also on
  `dis-canonical`.

## Open questions (for plan mode to resolve)

1. `dis-rls` session API and async wiring. Read any existing `dis-rls` stub and
   the live `dis-core` surface. Decide the session helper's shape (context
   manager signature, how the per-tenant scope is set under SQLAlchemy 2.0 async,
   commit and rollback semantics, what role the helper connects as), and confirm
   it satisfies hard rules 1 and 12. State the minimal surface; do not build the
   batched-by-tenant wrapper or standalone enforcement assertions unless a current
   consumer needs them.

2. Isolation test design (non-vacuous). Decide which RLS-protected table the test
   writes to (not necessarily canonical; weigh `bronze.*` for lower seeding burden
   against canonical for representativeness, and name the seeded mapping row if
   canonical is chosen), how cross-tenant rows are seeded (which role seeds, how
   identity FK targets come from the Slice 2 seeder), and how the test proves
   isolation independently of the helper it exercises. Confirm the helper connects
   as a NOBYPASSRLS role and that the test would fail if the scope were not set or
   if the role could bypass RLS. Confirm the live RLS posture of the chosen table
   and of `config.source_mappings` by introspection.

3. `dis-pii` detection source and gate. Confirm, by introspecting the live schema
   and reading the existing `dis-pii` stub, where a source mapping records that a
   column is PII (whether such a flag exists at all). If it does not exist in the
   applied schema, that is a schema-versus-decision drift: register it, do not
   edit DDL. Define what "configured backend" means for the gate in the
   no-implementation interim (presence of an injected placeholder, not real
   storage, and not a permissive default), and confirm the placeholder seams stay
   inert. Confirm `dis-pii` takes the mapping as an argument and does no DB access.

4. `dis-storage` path, signed URLs, and the emulator. Confirm the canonical
   path-builder shape against the frozen scheme (hard rule 9) and any contract,
   not from memory, with the caller supplying `trace_id`. Separate signed-URL
   issuance (deterministic, offline-testable: a well-formed URL with correct
   expiry) from the PUT/GET round-trip through that URL (emulator-dependent).
   Resolve whether the local GCS emulator honours the round-trip: if it does, the
   test exercises it; if not, defer that sub-check to real GCS and name it, while
   issuance and object access via the wrapper still hold (criterion 6). Confirm
   the wrapper honours `STORAGE_EMULATOR_HOST`.

5. Dependency direction and placement. Confirm each lib's dependencies
   (`dis-core` only, or also `dis-canonical`), that `dis-pii` depends on neither
   `dis-rls` nor a DB, that the import graph is acyclic, and the exact reserved
   directory for each lib. Do not invent dirs.

6. PII posture register entry. The deferral of real PII handling, the fail-loud
   gate posture, and the unresolved one-way-versus-reversible question are
   asserted across build-guide, D24, and CLAUDE.md, but D24 as written carries
   none of them (it reads as one-way HMAC with erasure by key delete; build-guide
   names a deferred "token to ciphertext" backend; CLAUDE.md hard rule 2 names
   "per-tenant KMS keys"). Open a register entry with its own D-number that
   records this gap and leaves the long-term posture OPEN with its trigger; do not
   settle the posture here. Also note the D24-versus-D18 mis-citation in
   architecture for correction (settle nothing, just register).

7. `identity_mirror` RLS posture contradiction (carry, do not settle here).
   Build-guide Slice 7 calls `identity_mirror` RLS-protected, while Slice 1 / 2
   introspection found it not RLS-protected. `dis-rls` sits at the center of this,
   so confirm the live posture by introspection and, if the contradiction stands,
   flag it for the Slice 7 register rather than resolving it in this slice.
