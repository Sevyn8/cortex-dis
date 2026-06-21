# Slice 10: Streaming consumer happy path (streaming-consumer)

## Depends on

- Slice 9b (`csv-ingest-worker`), built and pushed: the upstream producer of
  `ingress.ready` and the bronze pointer. `ingress.ready` carries the resolved internal
  UUID identity (`tenant_id`/`store_id`), the external codes as optional fields (D52),
  `trace_id`, and the pointer to the bronze row plus the GCS object. Slice 10 is the
  first consumer of that corrected envelope (hard rule 10). It consumes the event; it
  does not re-resolve identity and does not mint a `trace_id` (it reads `trace_id` off
  the event, hard rule 4).
- Slice 5 (`dis-mapping`, `dis-validation`), built: the pure four-sub-stage mapping
  engine (rename, normalize, cast, derive) and the two Pandera suite types (pre-mapping
  source-shape, post-mapping canonical-shape), suite materialization from a handed-in
  definition, the canonical-shape derivation from `dis-canonical` plus authored
  invariants, the drift guard, and the failure formatter. Slice 5 explicitly deferred to
  Slice 10: loading mapping config and suites from `config.source_mappings` (the
  consumer owns the DB read as a refreshing side-input, D6), routing failures to
  quarantine, the atomic dual-write, `mapping_version_id` stamping, audit emission, and
  the B2 normalization pass-threshold / chunk-versus-row routing decision. `dis-mapping`
  does not stamp `mapping_version_id` and emits only columns the live canonical schema
  carries; Slice 10 stamps it (D22).
- Slice 4 for `dis-storage` (fetch the bronze chunk's GCS object, hard rule 9; paths
  parsed via `parse_object_path`, 9a/D53) and `dis-rls` (the RLS-aware session for the
  canonical writes, hard rules 1 and 12). `dis-pii` is not consumed here: tokenization
  happens at the receiver before bronze (D24), so the engine operates on already-
  tokenized data (confirm in plan mode).
- Slice 6 for `dis-audit` (fire-and-forget audit at each stage, hard rule 11). The live
  `audit.events` outcome vocab is `SUCCESS`/`FAILURE`/`SKIPPED`/`RETRIED` and cannot
  represent `DUPLICATE_*` or carry `prior_trace_id` and the dedup-key columns (D42),
  which is Slice 10's to resolve.
- Slice 3 for `dis-core` (`errors` root, `ids` for any minted UUIDs, `trace_id` read and
  propagation, `timestamps`, structured `logging`) and `dis-canonical` (the canonical
  models mirroring the live schema: the dual-write targets and the
  `mapping_version_id NOT NULL` columns).
- Slice 2 for the fixture seeder (seeds `identity_mirror` tenants/stores so the canonical
  write's composite `(tenant_id, store_id)` FK resolves, D39, and a
  `config.source_mappings` active mapping row so `mapping_version_id` has a source) and
  the test publishers; no real Customer Master and no Identity Service are touched.
- Decisions this slice must honour: D30 (atomic dual-write, one Cloud SQL transaction,
  RLS covers both, either-or-neither); D22 (`mapping_version_id` stamped on every
  mapping-produced canonical row); D33 (event tables append-only, latest-wins at read
  time over `(tenant_id, store_id, source_id, source_event_id)`); D39 (no-orphan:
  composite `(tenant_id, store_id)` FK to `identity_mirror.stores` at the canonical
  write); D6 (mapping config read as a refreshing side-input); D5 (bronze is the
  recoverable source the consumer fetches from); D13 (the consumer is where semantic
  validation lives; the permissive receiver deferred it).
- Decisions this slice must RESOLVE or carry (load-bearing; do not paper over): D38
  (event-table dedup columns `source_id`/`source_event_id` absent from the live schema;
  hard deadline before this slice begins; the D33 read-time window cannot be computed
  until resolved); D42 (audit duplicate-detail fields and the `DUPLICATE_*` outcome
  vocab absent; the drift-guard type-narrowing limit also lands here); D60 (`tenant_id`
  named as the Pub/Sub ordering key but no producer sets one; the first consumer either
  implements the convention end-to-end or strikes the description).
- CLAUDE.md hard rules: 1 and 12 (canonical reads/writes via `dis-rls` with tenant
  context), 5 (`mapping_version_id NOT NULL` on mapping-produced rows, D22), 6 (atomic
  dual-write, never split, D30), 7 (event tables append-only, latest-wins at read, D33),
  8 (any BigQuery via `BqClient` only, not expected here, canonical is Cloud SQL), 9 (GCS
  via `dis-storage` only), 10 (`ingress.ready` frozen, consume never change), 4
  (`trace_id` read, never minted), 11 (audit fire-and-forget). Code-quality rules: 3
  (tests in the same commit), 4 (no silent fallbacks for required values), 5 (errors
  carry `tenant_id`, `trace_id`, and the load-bearing id), 6 (no swallowed exceptions
  except audit), 7 (one concern per function).
- Downstream: Slice 11 (quarantine path) consumes the `quarantine` topic and drains
  failing rows to Cloud SQL `quarantine.*`; the per-row-versus-chunk routing and the B2
  pass-threshold are Slice 11's, not this slice's (see Scope). Slice 12 (replay) consumes
  `ingress.resubmit`; Slice 10 consumes `ingress.ready` only.

## Goal

After this slice, a standalone `streaming-consumer` service consumes `ingress.ready`,
fetches the bronze chunk from GCS, looks up the active per-(tenant, source) mapping by
version from `config.source_mappings` as a refreshing side-input (D6), runs the
pre-mapping source-shape Pandera suite, applies the `dis-mapping` four sub-stages, runs
the post-mapping canonical-shape Pandera suite, stamps `mapping_version_id` on every
produced canonical row (D22), and atomically writes the hot-table upsert and the matching
event-table insert in a single Cloud SQL transaction under the event's tenant (D30), with
audit at each stage. A valid chunk lands in canonical (the hot table plus the matching
event table); identity is enforced by the composite `(tenant_id, store_id)` FK to
`identity_mirror.stores` (D39); RLS scopes every read and write (hard rules 1 and 12). A
redelivered `ingress.ready` does not corrupt canonical: at-least-once is absorbed by the
event-table read-time dedup posture (D33, paired with D30), not by transactional
idempotency.

This is the happy path (build-guide Slice 10). The consumer is the only place
per-(tenant, source) transformation logic lives (architecture 4.6). It reads `trace_id`
off the event and mints none (hard rule 4). It consumes `ingress.ready` only;
`ingress.resubmit` and replay are Slice 12.

What it does not do: it does not build the `quarantine`-topic publish, the
per-row-versus-chunk routing, or the normalization pass-threshold (B2), all Slice 11; it
defines only a minimal safe disposition for a failing chunk so redelivery and poison
messages have a defined fate. It does not implement the circuit-breaker plus
`pipeline.dlq` backpressure pattern (D27, carried forward). It calls no Identity Service
and adds no stale-while-error fallback (D28, Slice 13); identity arrives resolved on
`ingress.ready` and existence is enforced by the FK (D39). It builds no
named-custom-transform escape hatch and no proactive schema-drift watcher (both deferred,
see Scope). It edits no DDL: D38 and D42 are resolved by deriving from the live schema and
registering the chosen mechanism in plan mode; if either genuinely requires a column the
schema lacks, the gap is surfaced and registered as a migration prerequisite, not authored
under Slice 10's service code.

## Task

Build the consumer in the directory the repo reserves for it
(`services/streaming-consumer/`); confirm the exact placement and the current scaffolding
state in plan mode rather than assuming it (repo-structure lists `pipeline/`, `sinks/`,
`health/`, `clients/`, but the tree may be skeletal). Decompose:

1. **Trigger intake (`ingress.ready`).** Subscribe to the `ingress.ready` topic and
   consume the event (first consumer, hard rule 10). Read the UUID identity, the optional
   codes, `trace_id`, and the bronze/GCS pointer off the event; do not re-resolve, do not
   mint. Confirm local Pub/Sub delivery against the live stack (the `ingress.ready` topic
   exists from 9b; confirm a subscription is created and that a test can publish and the
   consumer receive); the delivery proof errors, never skips, if the topic or
   subscription is absent. `ingress.resubmit` (replay) is not consumed here (Slice 12).
   D60 is resolved alongside (Task 0 below).

   0. **D60 ordering key (resolve before the rest is proven).** As the first real
      consumer, decide whether to honour the `tenant_id` Pub/Sub ordering-key convention
      end-to-end (amend 9b's `ingress.ready` publish to set the ordering key, a change to
      a shipped service, plus the consumer honouring ordering) or strike the contract's
      ordering-key description. Record which; if end-to-end, scope the 9b producer
      amendment and its blast radius. Plan-mode decision (open question 5).

2. **Bronze fetch.** Resolve the bronze pointer from `ingress.ready` to the GCS object
   and fetch the chunk via `dis-storage` (hard rule 9); paths parsed via
   `parse_object_path` (9a), never hand-split. Bronze is the recoverable source (D5). The
   fetch is read-only; the chunk is already tokenized at the receiver (D24), so no
   `dis-pii` dependency is taken (confirm in plan mode).

3. **Mapping config load (refreshing side-input).** Look up the active mapping for
   (tenant, source) by version from `config.source_mappings` (D6); the active mapping is
   the latest `status=active`, versions immutable (architecture 4.7). Derive the live
   `config.source_mappings` shape, the version column, and the suite-reference linkage in
   plan mode (open question 3). `mapping_version_id` for stamping (D22) is sourced from
   the loaded mapping row, never hardcoded; an absent active mapping is a required value
   (raise a `dis-core` error, never a silent fallback, code-quality rule 4). Decide the
   refresh mechanism (event-driven via `mapping.changed`, D6, or per-lookup/TTL) in plan
   mode; if per-lookup suffices for v1.0, defer event-driven with a trigger.

4. **Pre-mapping validation (source-shape).** Materialize the source-shape suite from the
   mapping's handed-in definition (Slice 5) and run it on the fetched chunk before
   mapping. Structural drift fails here (semantic validation lives in the consumer, D13).
   A failure routes to the minimal failure disposition (Task 8), not a quarantine publish
   (Slice 11).

5. **Mapping (four sub-stages).** Apply `dis-mapping` (rename, normalize, cast, derive) to
   the validated chunk (Slice 5, Polars engine). Declarative-only: no
   named-custom-transform escape hatch (deferred). Format drift surfaces as a
   normalization failure, routed to the failure disposition.

6. **Post-mapping validation (canonical-shape).** Run the canonical-shape suite (derived
   from `dis-canonical` plus authored invariants, Slice 5) on the mapped frame. A failure
   routes to the failure disposition. The drift guard (Slice 5) catches canonical schema
   drift; the D42 drift-guard type-narrowing limit (a narrowed column type passes the
   name-set match and is caught only at INSERT, then swallowed by fire-and-forget) is
   recorded.

7. **`mapping_version_id` stamp plus atomic dual-write.** Stamp `mapping_version_id`
   (BIGINT NOT NULL, D22, hard rule 5) on every produced canonical row. In one Cloud SQL
   transaction under the event's tenant via `dis-rls` (hard rules 1 and 12; D30, hard
   rule 6): UPSERT the hot table (`store_sku_current_position`, column-scoped merge,
   event-time-wins) and INSERT the matching event table (`store_sku_sale_events` for
   sales, `store_sku_change_events` otherwise), append-only, no UNIQUE (D33, hard rule 7).
   Either-or-neither: on any failure both roll back. The composite
   `(tenant_id, store_id)` FK to `identity_mirror.stores` enforces no-orphan at the write
   (D39); a row whose (tenant, store) is absent fails loud. D38: the event-table dedup key
   `(tenant_id, store_id, source_id, source_event_id)` is resolved here per plan mode
   (computed from live columns, or a registered migration prerequisite); record the
   resolution closing D38. Target safety: DIS on 5433, never Customer Master on 5432,
   asserted positively (Slice 7 pattern). Derive the hot-table natural key, the event-
   table partition and typed-shortcut columns, the sale-versus-change routing, and the
   dual-write column sets from the live schema in plan mode (open question 2).

8. **Failure disposition (minimal, not quarantine routing).** A chunk that fails pre-
   validation, mapping, or the canonical write gets a defined, safe fate: audit the
   failure (fire-and-forget) plus a non-ack / redeliver-or-dead-letter posture so the
   message is neither silently dropped nor able to corrupt canonical. The `quarantine`-
   topic publish, the per-row-versus-chunk split, and the B2 pass-threshold are Slice 11;
   do not build them. Name the minimal disposition in plan mode (open question 6) without
   inventing the quarantine path.

9. **Audit emission.** Emit a fire-and-forget audit event at each stage (intake, fetch,
   pre-validation, mapping, post-validation, dual-write, failure disposition), each
   carrying `tenant_id` (D43), `trace_id`, `mapping_version_id` where known, and the
   load-bearing id (code-quality rule 5; hard rule 11). On a duplicate event-table
   dedup-key hit, D33 specifies `DUPLICATE_NOOP`/`DUPLICATE_OVERWRITTEN` plus
   `prior_trace_id`; the live `audit.events` cannot represent these (D42). Resolve D42 in
   plan mode: land the duplicate detail in the `event_data` JSONB (no DDL) or elect a
   registered DDL change extending the outcome CHECK / adding columns; record the
   resolution. Audit failures are logged, never raised; duplicates tolerated (D44).

## Acceptance criteria

1. The service is importable and runs as a standalone Pub/Sub-subscribed consumer on the
   `ingress.ready` topic. Its test directory is collected by the runner, with a check
   proving collection (an excluded dir reports green having run nothing, Slice 7 lesson).
   `make check` shows no tier regression and the new tests pass. The service passes
   `mypy --strict` under the 9d gate. The service `CLAUDE.md` records its new invariants
   before slice exit (under 100 lines).
2. Given an `ingress.ready` event and a seeded bronze chunk at the pointer, the consumer
   reads identity and `trace_id` off the event (a test confirms the emitted `trace_id`
   equals the event's, that the consumer mints none, and that it makes no Identity
   Service call) and fetches the chunk via `dis-storage`. The delivery proof errors,
   never skips, when the topic or subscription is absent.
3. A valid chunk produces canonical rows: the hot-table upsert and the matching event-
   table insert land in one transaction. A test asserts both present after commit and
   both absent after an induced mid-transaction failure (either-or-neither, D30).
   `mapping_version_id` is NOT NULL on every produced row and equals the loaded mapping's
   version (D22); a test asserts the stamp.
4. The no-orphan FK holds (D39): a chunk whose `(tenant_id, store_id)` is present in
   `identity_mirror.stores` writes, and one whose pair is absent fails loud at the write;
   a test seeds both. RLS isolation: a test proves a write under tenant A is invisible to
   a tenant-B-scoped read (hard rules 1 and 12).
5. Pre-mapping source-shape validation rejects a structurally wrong chunk and accepts a
   well-formed one (test both ways); post-mapping canonical-shape validation rejects a
   chunk that maps to an invalid canonical frame and accepts a valid one (test both ways).
   That validation lives in the consumer, not the receiver, is the D13 posture.
6. Mapping applies the four sub-stages via `dis-mapping`; a test asserts a representative
   rename, normalize, cast, and derive against a seeded mapping. That no named-custom-
   transform escape hatch exists is a review-only property (a test cannot prove a
   feature's absence), confirmed by an import or scope check.
7. The event-table dedup key is resolved (D38): the read-time latest-wins window over
   `(tenant_id, store_id, source_id, source_event_id)` is computable against the live
   schema (a test asserts a corrected event row is the latest-wins survivor at read), OR
   the resolution is a registered migration prerequisite and the slice does not proceed
   past plan mode until it lands. The resolution closes D38; it is recorded.
8. At-least-once redelivery of the same `ingress.ready` does not corrupt canonical: a
   redelivered chunk lands as an append-only event row deduped at read (D33) and the hot
   table reflects event-time-wins, not a double-count; a test redelivers and asserts read-
   time truth. Transactional idempotency is deliberately not the mechanism (D30); this is
   review-confirmed and asserted by the redelivery test.
9. A failing chunk reaches the minimal safe disposition: audited and non-acked or dead-
   lettered, never silently dropped, never partially written; a test induces a validation
   failure and asserts no canonical row and a defined disposition. That the `quarantine`-
   topic publish is absent is review-only (Slice 11).
10. Audit is emitted fire-and-forget at each stage carrying `tenant_id`, `trace_id`, and
    `mapping_version_id` where known; a test injects an audit failure and confirms the
    data path completes (logged, not raised). The D42 duplicate-detail resolution
    (`event_data` JSONB or a registered DDL change) is recorded, and a test asserts the
    duplicate path emits the chosen representation. Duplicate audit rows are tolerated
    (D44).
11. D60 is resolved: either the `tenant_id` ordering-key convention is honoured end-to-end
    (a test asserts the key is set on publish and consumed in order) or the contract's
    ordering-key description is struck; the resolution is recorded, and if end-to-end the
    9b producer amendment is noted.
12. The service raises `dis-core` errors (no raw `RuntimeError`/`ValueError`), binds
    `tenant_id`, `trace_id`, `service`, `stage` in logs, logs no PII or raw payloads, and
    mints any IDs via the `dis-core` `ids` helper (not `trace_id`, which it reads).

## Scope boundary

In scope:
- The `streaming-consumer` service: `ingress.ready` intake, bronze/GCS fetch, the mapping
  config load (refreshing side-input), the pre- and post-mapping Pandera suites wired, the
  `dis-mapping` four sub-stages wired, `mapping_version_id` stamping, the atomic dual-write
  to the hot table plus the matching event table, the no-orphan FK and RLS enforcement,
  read-time latest-wins dedup (D33) once D38 is resolved, the minimal failure disposition,
  and per-stage fire-and-forget audit.
- Resolving D38 (event-table dedup key) and D42 (audit duplicate detail plus the drift-
  guard type-narrowing limit), and resolving-or-striking D60 (ordering key). Plan mode
  derives from the live schema; a genuine DDL need is surfaced and registered as a
  migration prerequisite, not authored here.
- The tests proving the dual-write either-or-neither, the version stamp, the FK no-orphan,
  RLS isolation, both validation gates, mapping, read-time dedup under redelivery, the
  failure disposition, and audit fire-and-forget.
- The service `CLAUDE.md` invariants.

Out of scope (do not let the slice sprawl):
- The `quarantine`-topic publish, the per-row-versus-chunk routing, and the B2
  normalization pass-threshold. *Slice 11.* Slice 10 defines only the minimal safe failure
  disposition. (Note: Slice 5's out-of-scope notes pointed "routing failures to
  quarantine" and B2 at Slice 10; the build-guide scopes the quarantine path at Slice 11.
  The build-guide wins; this pointer is carried, not honoured as written, and the Slice 5
  pointer should be struck or re-pointed to Slice 11.)
- The quarantine drainer service (writes `quarantine.*` in Cloud SQL). *Slice 11.*
- `ingress.resubmit` consumption and replay tooling. *Slice 12.*
- The circuit-breaker plus `pipeline.dlq` backpressure pattern and the Cloud SQL health
  probe (D27). The happy-path consumer does not divert on infra-failure; backpressure is
  carried forward (v1.0 launch operates with manual recovery; the Phase 3 auto-drainer
  activates it). *Carried forward (D27).*
- Any Identity Service call and the stale-while-error fallback (D28). Identity arrives
  resolved on `ingress.ready`; existence is enforced by the FK (D39). The real Identity
  Service is *Slice 13.*
- The named-custom-transform escape hatch and its registry. Declarative-only: a gap the
  bounded vocabulary cannot express is an onboarding problem (fix the mapping or extend the
  shared vocabulary) or schema drift (detect and fail), not a per-source code path.
  *Deferred. Trigger: a concrete source that defeats both the declarative vocabulary and a
  vocabulary extension.* (To be recorded in build-guide and decisions.md.)
- Proactive schema-drift monitoring (a standalone watcher or a pre-processing schema
  comparison). Drift is caught reactively: structural drift by the source-shape suite,
  format drift by normalization, both into the failure disposition (and the quarantine
  path at Slice 11). *Deferred. Trigger: reactive detection proves insufficient in pilot (a
  drift class slips past both suites), or a tenant SLA requires proactive drift alerting.*
  (To be recorded in build-guide and decisions.md.)
- `mapping.changed` event-driven cache refresh, if plan mode finds per-lookup or TTL
  refresh sufficient for v1.0. *Deferred if so. Trigger: mapping-edit-to-effect latency
  proves too slow, or config read load justifies event-driven invalidation (D6).*
- PII tokenization or any `dis-pii` dependency. Tokenization is at the receiver before
  bronze (D24); the engine operates on tokenized data. *Confirm in plan mode; not built
  here.*
- Daily compute and `signal_history` (D31, D32), nightly batch and the BigQuery archive
  (D29, Slice 21), and any `BqClient` use. Canonical is Cloud SQL here.
- Authoring or changing any DDL. A column D38, D42, or the dual-write needs but the schema
  lacks is surfaced and registered as a migration prerequisite, not added here.

## Constraints

- Canonical reads and writes go through `dis-rls` with the tenant context set from the
  event's UUID tenant (hard rules 1 and 12); never call SQLAlchemy directly against
  canonical schemas (CI lint forbids). Target safety: DIS on 5433, never Customer Master on
  5432, asserted positively.
- The dual-write is one transaction, either-or-neither (D30, hard rule 6); never split the
  hot upsert and the event insert.
- Event tables are append-only with no UNIQUE; latest-wins is applied at read time via the
  D33 window; corrections are separate rows (hard rule 7).
- `mapping_version_id` is BIGINT NOT NULL on every mapping-produced row, sourced from the
  loaded mapping (D22, hard rule 5); never hardcoded, never null.
- `ingress.ready` is a frozen contract (hard rule 10): consume, never change; a needed-but-
  absent field is surfaced and registered, not improvised.
- `trace_id` is read off the event, never minted mid-pipeline (hard rule 4); UUIDs via the
  `dis-core` `ids` helper.
- All GCS access is via `dis-storage` (hard rule 9); paths are parsed via
  `parse_object_path` (9a), never improvised or hand-split.
- The mapping config is a required value: an absent active mapping raises a `dis-core`
  error, never a silent fallback (code-quality rule 4). The side-input read and the
  Pub/Sub delivery proof error, never skip, when their backing store or mechanism is absent
  (Slice 4 and 7 lesson).
- Audit is fire-and-forget (hard rule 11): logged never raised, never blocking; duplicates
  tolerated (D44); every event carries `tenant_id` (D43), `trace_id`, and the load-bearing
  id (code-quality rule 5).
- At-least-once is absorbed by D33 read-time dedup, not transactional idempotency (D30); a
  redelivered chunk must not double-count the hot table.
- Errors from `dis-core/errors.py`; structured logging binds the four keys; never log PII
  or raw payloads. Code passes the `mypy --strict` gate (9d).
- Live contact is the local stack (Postgres on 5433, the GCS and Pub/Sub emulators, the
  Slice 2 fakes and seeder); plan-mode introspection is read-only against `ithina_dis_db`
  on 5433.
- The Polars and Pandera stacks stand (Slice 5); this slice does not relitigate them.
- Service `CLAUDE.md` under 100 lines; new invariants captured before slice exit.

## Open questions (for plan mode to resolve)

1. **D38 resolution (blocking; hard deadline before the slice begins).** Introspect the
   live canonical event tables: is `(tenant_id, store_id, source_id, source_event_id)`
   computable from existing columns (for example deriving `source_id` from the channel and
   `source_event_id` from `transaction_id`/`line_item_seq` or a source timestamp), or does
   the dedup key require a migration adding `source_id`/`source_event_id`? Decide and
   record the resolution closing D38. If a migration is required, it is a named
   prerequisite that must land before the dual-write and read-time-dedup proofs; the slice
   does not proceed past plan mode until it does. No DDL is authored under Slice 10's
   service code.
2. **The live canonical write shape.** Derive the hot-table natural key (architecture names
   `(tenant_id, store_id, sku_id, sku_variant, sku_lot_batch)` with NULLS NOT DISTINCT) and
   its column-scoped-merge / event-time-wins upsert, the two event tables' partition and
   typed-shortcut columns, the sale-versus-change routing, and the `mapping_version_id`
   column on each target, all from the live schema.
3. **`config.source_mappings` shape and the version/suite linkage.** Derive the live
   columns: how the active mapping is selected (`status=active`, latest version), how
   `mapping_version_id` is read, and how the pre- and post-mapping suite definitions are
   referenced from the mapping row. Decide the refresh mechanism: event-driven
   (`mapping.changed`, D6) or per-lookup/TTL; if per-lookup suffices for v1.0, defer
   event-driven with a trigger.
4. **D42 resolution.** Decide where the duplicate-audit detail (the `DUPLICATE_*`
   distinction, `prior_trace_id`, the dedup key, `row_hash`) lands: the `event_data` JSONB
   (no DDL) or a registered DDL change extending the outcome CHECK / adding columns. Record
   the resolution. Also record the drift-guard type-narrowing limit and decide whether to
   address it here or carry it.
5. **D60 resolution.** Decide: implement the `tenant_id` ordering-key convention end-to-end
   (amend 9b's `ingress.ready` publish to set the ordering key, a change to a shipped
   service, plus the consumer honouring ordering) or strike the contract's ordering-key
   description. Record which; if end-to-end, scope the 9b producer amendment and its blast
   radius.
6. **The minimal failure disposition.** Name what a failing chunk does inside Slice 10
   without building quarantine routing: audit-and-nack, dead-letter, or leave-unacked-for-
   redelivery. It must neither silently drop nor partially write. The quarantine publish,
   the per-row-versus-chunk split, and the pass-threshold (B2) are Slice 11.
7. **Identity posture on the happy path.** Confirm that `ingress.ready`'s resolved UUID
   identity plus the composite FK (D39) is the v1.0 enforcement and that no Identity
   Service call or D28 fallback is needed here (Slice 13 owns the real Identity Service).
   Confirm the consumer does not re-resolve identity.
8. **Service scaffolding state.** Confirm the live `services/streaming-consumer/` tree
   against repo-structure (`pipeline/`, `sinks/`, `health/`, `clients/`): which files
   exist, and that Slice 10 builds only the happy-path surface (fetch, mapping, normalize,
   validate_pre, validate_post, the canonical sink, audit), leaving `quarantine.py`,
   `dlq.py`, and `circuit_breaker.py` for Slice 11 and the D27 carry-forward. Place code in
   the reserved dirs; invent none.
9. **Bronze chunk fetch and grain.** Confirm how `ingress.ready`'s pointer resolves to the
   GCS object and the bronze row, and the chunk grain (one ingress chunk yields N canonical
   rows). Confirm the chunk is already tokenized (D24) so no `dis-pii` dependency is taken.
10. **At-least-once versus the dual-write.** Confirm the redelivery proof: a redelivered
    `ingress.ready` re-runs the pipeline and lands an append-only event row deduped at read
    (D33), with the hot table event-time-wins and no double-count. Confirm transactional
    idempotency is deliberately not used (D30).
