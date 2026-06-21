# Slice 7: Mirror Sync Consumer, DB-pull mode

## Depends on

- Slice 1 for the applied `identity_mirror` schema and the migration that created
  it, plus the DIS Postgres roles. The source of truth for the mirror's columns,
  keys, and FK targets is the live DIS schema (`ithina_dis_db`, port 5433), not the
  DDL files; re-derived by introspection in plan mode.
- Slice 2 for the test fixture seeder (the test-only way other slices populate
  `identity_mirror`; it is not this service and this service does not call it — this
  slice is the runtime path the seeder stands in for). The DB-pull **read**, however,
  is exercised against a **Customer-Master-shaped test Postgres harness** — an
  in-cluster `ithina_platform_db` database on 5433 (`core.tenants` / `core.stores` with
  FORCE RLS + the platform policy, seeded), provisioned by
  `dis_testing.customer_master_db`. The Slice 2 Customer Master *fake* is HTTP-only
  (JWTs / sessions / events) and cannot serve a direct DB read, so it is **not** what
  DB-pull is tested against. No test touches a live real Customer Master (port 5432).
  The harness is reusable: a later CM-reading slice reuses it rather than rebuilding it.
- Slice 3 for `dis-core` (trace_id mint and contextvar, `ids` for UUIDv7 where
  needed, structured logging, the single `DisError` root). Reused without
  disturbance.
- Slice 6 for `dis-audit`. This is the first service slice, and audit emission is
  service-layer and fire-and-forget (failures logged, not raised). The sync emits
  audit at run boundaries.
- The Customer Master read contract: a direct, read-only Postgres connection run
  under a platform read context. Customer Master is the source of truth for tenant
  and store identity and lifecycle. The contract and the live CM schema are the
  authority for what is read and how it is read; re-confirmed by introspection in
  plan mode, not from this doc.
- CLAUDE.md hard rules that bear on a service reading one Postgres instance and
  writing another: errors from `dis-core`; UUIDv7 via `dis-core` where IDs are
  minted; no PII or raw payloads in logs; the target-safety discipline on every
  Postgres-touching path.
- D35 (two modes; DB-pull is the v1.0 launch mode, the Pub/Sub consumer is
  deferred), D39 (the canonical composite store FK, for which the mirror is the
  target), and D12 (the mirror plus a real FK as the cross-database integrity
  substitute). D41 and the `is_active` doc claim are handled as register actions
  below, not inherited as constraints.

## Goal

After this slice, `identity_mirror.tenants` and `identity_mirror.stores` can be
brought into faithful agreement with Customer Master's tenants and stores by
running the Mirror Sync Consumer in DB-pull mode. The same run serves first load
and periodic reconciliation. Once populated, canonical, bronze, and config rows
can reference real local identity rows through the engine-enforced FKs (D12, D39),
without the test-only fixture seeder. Customer Master remains the source of truth;
the mirror is a read-derived replica that is never written back to CM. In this
mode the sync runs as a finite batch process: one invocation starts, does one sync
pass, and exits, so it does not run continuously and does not need to live between
runs. The Pub/Sub consumer mode is not built in this slice, and no receiver,
streaming, or UI logic is built. This slice reads Customer Master's Postgres and
writes the DIS Postgres, so the target-safety discipline applies on both sides.

## Task

Build the DB-pull mode of the `mirror-sync-consumer` service, in the directory the
repo already reserves for it; confirm exact placement and module layout in plan
mode rather than inventing structure.

1. A read path that connects to Customer Master read-only under the platform read
   context and reads the tenant and store records CM returns. The context-setting
   is transaction-local and must be set, or the read silently returns nothing; the
   run treats an unset or mis-set context as a loud failure, not an empty mirror.
   The exact session and context mechanism is derived in plan mode.
2. An upsert path into `identity_mirror.tenants` and `identity_mirror.stores`:
   insert new records, update changed ones, on the natural keys. It never deletes
   and never soft-deletes. Lifecycle is whatever Customer Master's status says,
   replicated as-is; there is no DIS-side active or inactive flag. The exact field
   mapping and conflict targets are re-derived from both live schemas in plan mode.
3. A run entrypoint that runs to completion and exits: it runs on demand for first
   load and is shaped to be driven by a scheduler or an external trigger for
   periodic reconciliation, with the same upsert path serving both. The process
   does one sync pass per invocation and terminates with a meaningful exit status;
   it holds no resident loop or subscription in this mode. The host (a scheduled or
   event-triggered job on Cloud Run, Kubernetes, or an equivalent runner) and the
   scheduler or trigger wiring are not fixed by this slice.
4. Audit emission at run boundaries (start and end, with counts), service-layer and
   fire-and-forget per `dis-audit`.
5. The Pub/Sub consumer mode is not implemented. Whether its directory seam is
   scaffolded as inert stubs or left out entirely is a plan-mode call against the
   build-guide and D35; if scaffolded, it makes no live subscription and no I/O.

## Acceptance criteria

Each criterion maps to one verification, defined in plan mode.

1. A first-load run against the Customer Master fake populates
   `identity_mirror.tenants` and `identity_mirror.stores` with every record CM
   returns under the platform context; the mirrored counts equal what CM returns.
   Verified by an independent re-read of both sides, not by the sync agreeing with
   its own bookkeeping.
2. A re-run after CM records change converges the mirror: new records inserted,
   changed records updated on the natural keys, with no duplicate rows and no
   deletions.
3. Idempotence: a re-run with no CM change makes no spurious writes; the mirror is
   unchanged. Re-running is safe and cheap, which is the reconciliation use case.
4. The read uses the platform read context. A run whose CM context is unset or
   mis-set fails loud before writing, rather than emptying or partially filling the
   mirror from a zero-row read. This proof errors if its dependency (the CM
   connection or fake) is absent; it does not skip and does not fall back to a
   guessed default.
5. Target safety: the run reads the Customer Master database and writes the DIS
   database, each guarded so the wrong target is impossible, not merely unlikely. A
   mix-up fails before any write. The read side positively asserts it is on the
   Customer Master database; the write side asserts it is on the DIS database on
   5433.
6. Audit events are emitted at run start and end. An audit-write failure is logged,
   not raised, and does not fail the sync.
7. Mirrored rows satisfy the canonical FK expectations (D12, D39): tenant and store
   keys land such that a canonical write can reference them under the composite
   store FK. Verified by a referencing write succeeding against mirrored rows and
   failing against an absent identity.
8. Tests run against the Customer-Master-shaped test Postgres harness (the in-cluster
   `ithina_platform_db` on 5433, provisioned by `dis_testing.customer_master_db`) and
   the DIS database on 5433. No test touches a live real Customer Master (port 5432),
   and every Postgres-writing test targets the DIS database only, guarded. (The Slice 2
   Customer Master fake is HTTP-only and cannot serve the DB-pull read.)
9. The service raises `dis-core` errors (no raw `RuntimeError` or `ValueError`),
   binds `trace_id`, `service`, `stage` (and `tenant_id` where a per-tenant context
   applies) in logs, logs no PII or raw payloads, and mints any UUIDs via
   `dis-core`. The service `CLAUDE.md` records its new invariants before slice exit
   (under 100 lines).

## Scope boundary

In scope:

- DB-pull read of tenants and stores from Customer Master under the platform read
  context.
- Upsert into `identity_mirror.tenants` and `identity_mirror.stores`: insert and
  update on natural keys, never delete, never soft-delete, lifecycle via CM status
  replicated verbatim.
- A run-to-completion entrypoint (start, sync, exit) that runs on demand and is
  shaped to be driven by a scheduler or external trigger for reconciliation; one
  path for first load and re-pull, with no resident loop or held subscription.
- Audit emission at run boundaries.
- Target-safety guards on both the CM read connection and the DIS write connection.
- The service's own `CLAUDE.md` invariants.

Out of scope (deferred, each with its trigger):

- Pub/Sub consumer mode (subscribe to `identity.changed` and apply the same
  upserts). *Deferred. Trigger: Customer Master emits `identity.changed` (D35).*
- Mirroring Customer Master users or any identity table other than tenants and
  stores. *Deferred. Trigger: a slice needs a mirrored users table or another
  identity table.*
- External-identifier resolution: the translation between Customer Master's
  external identifiers (its slug-form codes, and/or the contract's `t_*` / `s_*`
  form) and the internal UUID keys (D37). Not needed for replication, which copies
  CM's own columns. *Deferred. Trigger: the first receiver or consumer that must
  resolve identity from an external identifier (see register action on D37).*
- DIS-side handling of a Customer Master record that is hard-deleted or removed at
  source. The mirror keeps every row it has mirrored; it does not delete or
  mark-absent, because deleting a mirror row would orphan or cascade-delete the
  canonical, audit, and config rows that reference it (D12, D39). *Deferred.
  Trigger: Customer Master implements a delete or deactivation path AND a
  data-governance policy defines the referential cleanup (new register entry
  below).*
- Any explicit drift detection or divergence reporting beyond convergence-by-upsert
  (for example a report of mirror rows whose CM source changed or vanished). The
  default is convergence-by-upsert only; whether any drift reporting is included is
  a plan-mode call.
- Scheduler or trigger wiring and the choice of host (Cloud Run Job, a Kubernetes
  Job or CronJob, or an equivalent runner). The run-to-completion shape that makes
  the process schedulable or triggerable is in scope; provisioning the scheduler or
  trigger, and fixing the host, is not. *Deferred. Trigger: ops decides the
  reconciliation cadence and the deployment target.*
- Any cloud connection credential or IAM binding for the Customer Master read
  replica. Local uses the read-only role by password; in GCP the CM connection
  resolves to the read replica (not the primary) via the platform-provided proxy or
  IAM path, env-driven and never hard-coded. The cloud credential is named, not
  assumed. *Deferred. Trigger: the first slice that provisions cloud
  infrastructure.*
- Authoring or changing any DDL on `identity_mirror`. The research gate found the
  mirror schema sufficient for replication and FK integrity, so no migration is in
  scope. If plan mode finds a missing column, surface and register it; do not edit
  DDL in this slice.

## Constraints

- Customer Master is the source of truth for tenant and store identity and
  lifecycle. The sync reads CM (a read-only role; the replica is read-only at the
  engine) and never writes back. Writing CM is structurally impossible and must not
  be attempted.
- The mirror is upsert-only: insert and update, never delete, never soft-delete.
  Lifecycle is represented by Customer Master's status, replicated as-is. There is
  no DIS-side `is_active` flag; the docs that name one are stale (register action
  below).
- Same code serves local and cloud (D35); connection profiles are
  environment-driven. The Customer Master connection target is environment-resolved
  with no hard-coded instance: locally it points at the local Customer Master
  Postgres, and in GCP it points at the Customer Master read replica, not the
  primary. The read replica is read-only by nature, which reinforces that writing
  CM is structurally impossible. The Slice 2 fixture seeder is test-only and is not
  this service; this service is the runtime path. There is no separate local seeder
  versus cloud sync.
- The Customer Master read runs under the platform read context, set
  transaction-locally. An unset or mis-set context returns zero rows silently, so
  the sync must fail loud on that condition rather than write an empty or partial
  mirror. A load-bearing proof of this errors on an absent dependency; it does not
  skip and does not fall back to a guessed default.
- This slice touches two Postgres instances, so the target-safety pass is item 1 of
  the plan: which database and port each connection uses, what the writing path
  does, and the guard that refuses the wrong target on each side. The read
  positively asserts the Customer Master database; the write asserts the DIS
  database on 5433. A store of identity is never written to 5432, and the DIS write
  never lands on Customer Master.
- In DB-pull mode the process is run-to-completion: one invocation does one sync
  pass and exits with a meaningful status, holding no resident loop or subscription.
  The host that runs it (a scheduled or event-triggered job on Cloud Run,
  Kubernetes, or equivalent) is not fixed by this slice. The Pub/Sub consumer mode,
  when later built, is the long-lived listener; DB-pull is deliberately the
  opposite, so the two share an upsert path but not a process model.
- `identity_mirror` is not RLS-protected (the research gate confirmed RLS off, no
  policies), so the upsert is a plain write as the DIS service role: no per-row
  tenant scoping, no distinct role for the mirror write (D41 register action below).
  This is independent of the Customer Master read context, which is CM's own RLS,
  not DIS's.
- Load-bearing schema and code claims carry their evidence inline (the introspected
  row, constraint, or policy, or the file and line), not a DDL line or a summary.
  Any "mirrors Customer Master" claim is verified against CM independently, both
  directions, not by the sync's own test.
- Errors inherit from the `dis-core` `DisError` root; no raw `RuntimeError` or
  `ValueError`. Structured logging binds `trace_id`, `service`, `stage` (and
  `tenant_id` where a per-tenant context applies); never log PII or raw payloads.
  UUIDs only via `dis-core`.
- Audit is service-layer and fire-and-forget (the Slice 6 posture): an audit-write
  failure is logged, not raised, and does not fail the sync.
- "Green" is a weak signal here. The count-match and idempotence criteria are the
  ones most able to pass vacuously, so they are verified by an independent re-read,
  and register gaps are logged with their own identifiers before commit, in the
  same pass.

## Register actions this slice logs

Logged before commit, each with its own evidence and identifier.

- D41 resolves. The research gate confirmed `identity_mirror.tenants` and
  `identity_mirror.stores` are RLS-off with no policies, consistent with the Slice
  1, 2, and 4 findings. Resolution: RLS-off on `identity_mirror` is correct, and
  the build-guide "RLS-protected" claim (with the per-row-versus-distinct-role
  plan-mode question premised on it) is the stale side. Mark D41 resolved with the
  finding and correct the build-guide text. Whether that text correction rides in
  this slice's commit or is register-only is an operator call at the commit gate.
- New entry: the `is_active` doc-versus-schema gap. The mirror-sync service docs,
  its README, and the architecture describe soft-delete via an `is_active` column;
  no such column exists on either mirror table, and lifecycle lives in `status`
  (with the per-lifecycle timestamps alongside). Register it as a doc-versus-schema
  gap with introspected evidence and the doc lines that claim `is_active`. The
  resolution is "lifecycle via status, upsert-only, never delete," which this slice
  implements; correct the docs accordingly.
- D37 re-deadline. The "before Slice 7" deadline was premised on Slice 7 needing
  the external-to-internal translation. The gate showed Slice 7 does not: it
  replicates Customer Master's own columns and needs no external-id resolution for
  FK integrity. Re-point D37's deadline to its real trigger, the first receiver or
  consumer that resolves identity from an external identifier. D37 stays OPEN; it is
  not this slice's to settle.
- New entry: deferred Customer-Master-deletion handling. Record that DIS keeps every
  mirrored row and does not delete or mark-absent, with the trigger (a CM delete or
  deactivation path plus a governance policy), and note the symmetry: the
  orphan-versus-cascade risk lands on Customer Master the same way, so a policy that
  solves it on one side without the other reintroduces the risk.

## Open questions (for plan mode to resolve)

1. Service structure and placement. Confirm the reserved directory and the module
   layout for the DB-pull mode (the read, the upsert, the run entrypoint), and
   whether the Pub/Sub consumer seam is scaffolded as inert stubs or left out,
   against the build-guide and D35. Do not invent structure.
2. The Customer Master read mechanism. Confirm, against the read contract and the
   live CM, how the platform read context is set transaction-locally on the CM
   connection, whether an existing DIS session helper is reused or a CM-specific
   read session is built, and that the read-only role is permitted to set the
   required context. Confirm the CM connection target is environment-resolved with
   no hard-coded instance: the local Customer Master Postgres locally, and the read
   replica (not the primary) in GCP. State the connection-profile shape for local
   and name (do not assume) the cloud profile.
3. The CM-to-mirror field mapping and upsert. Re-derive, from both live schemas
   independently and in both directions, the field mapping and the upsert conflict
   targets (the natural keys for tenants and for the composite-keyed stores),
   rather than taking them from this doc or the gate summary. Confirm the mapping
   leaves no DIS-depended attribute unmapped and that status and the lifecycle
   timestamps replicate without narrowing.
4. Fail-loud read posture and exit status. Decide how the run detects and fails on
   an unset or mis-set CM context (a zero-row read that should not be zero), so a
   misconfiguration cannot silently empty or under-fill the mirror, and confirm the
   entrypoint exits non-zero on this and on a target-safety mismatch so a scheduler
   or trigger detects the failure; a clean pass exits zero. Confirm the proof errors
   on an absent CM dependency rather than skipping.
5. Target safety on both instances. State which connection reads (CM) and which
   writes (DIS, 5433), the positive current-database assertion on each side, and
   how a mix-up fails before any write. Account for the Slice 1 current-database
   guard and the read-only grant as defense in depth.
6. FK-integrity verification. Decide how criterion 7 is proven: a canonical (or
   bronze) write referencing a mirrored tenant and store under the composite FK
   succeeds, and one referencing an absent identity fails. Confirm the referenced
   identities come from the sync run, not the test-only fixture seeder.
7. Reconciliation semantics. Confirm the slice delivers convergence-by-upsert for
   both first load and re-pull, and decide whether any drift reporting is in or out
   (default out). If out, say what a later drift-detection slice would add.
8. Audit shape. Confirm what the run emits at start and end (the event kind and the
   counts), consistent with `dis-audit`'s fire-and-forget posture, without naming
   HTTP or storage detail in the doc.
