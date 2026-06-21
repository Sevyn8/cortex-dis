# Slice 9: CSV upload — Phase 2 worker (csv-ingest-worker)

## Depends on

- Slice 8 (CSV upload Phase 1) only by contract, not by code. The single contract
  between the two halves is: a finalized CSV object exists at the canonical GCS
  path, with `trace_id` and identity recoverable from it, minted upstream. Slice 8
  is not built (deferred pending the dis-ui-server demand list), so this slice does
  not depend on Slice 8 code: tests seed the finalized object directly and the
  worker runs against it. The worker reads `trace_id`; it never mints one.
- Slice 4 for `dis-storage` (GCS object access and the fixed canonical path scheme,
  hard rule 9), `dis-rls` (the RLS-aware session for the bronze write, hard rules
  1 and 12), and `dis-pii` (the Slice 4 fail-loud gate, wired here, not extended).
- Slice 6 for `dis-audit` (fire-and-forget audit emission to Cloud SQL
  `audit.events`, hard rule 11).
- Slice 3 for `dis-core` (`errors` root, `ids` for any minted UUIDs, `trace_id`
  read and propagation, `timestamps`, structured `logging`).
- Slice 2 for the Identity Service fake (`resolve_from_upload`) and the fixture
  seeder (writes test tenants/stores into `identity_mirror` so the bronze write's
  identity resolves), both run in tests; no real Customer Master is touched.
- Decisions this slice must honour: D36 (Phase 2 is a standalone worker, event-
  triggered, identity inherited from the upload session, reads not mints
  `trace_id`); D5 (bronze-first: write then publish, so a lost publish is
  recoverable); D13 (permissive ingress: structural preflight only, semantic
  validation is the streaming consumer's); D24 and D40 (PII tokenization seam,
  fail-loud, no backend in v1.0; the CSV-flag path is inert because no authoritative
  per-column flag exists in the live schema, D40 limitation 2); D43 (every audit
  event carries a known `tenant_id`; the worker emits post-identity); D44 (audit
  duplicates are tolerated in Cloud SQL); D16 (DuckDB used for preflight).
- CLAUDE.md hard rules: 4 (`trace_id` read, never invented mid-pipeline), 9 (GCS via
  `dis-storage` only, the fixed path), 1 and 12 (bronze via `dis-rls` with tenant
  context), 10 (`ingress.ready` is a frozen contract: populate, never change),
  11 (audit fire-and-forget). Code-quality rules: 3 (tests in the same commit),
  4 (no silent fallbacks for required values), 5 (errors carry `tenant_id`,
  `trace_id`, and the load-bearing id), 6 (no swallowed exceptions except audit),
  7 (one concern per function).
- Downstream consumer: the streaming consumer (Slice 10) reads `ingress.ready` and
  the bronze pointer and owns mapping, validation, canonical writes,
  `mapping_version_id`, and failure routing. It sizes what the publish must carry.
  The worker does none of that.

## Goal

After this slice, a standalone worker service exists that turns a finalized CSV
object into a bronze record and an `ingress.ready` notification, idempotently, with
audit at each step. When a CSV object finalizes at the canonical GCS path, the
worker resolves the upload session's identity via the Identity Service, runs a
DuckDB structural preflight (does the object parse as CSV; plausible structure, row
count, type sniff), passes any recognized PII through the fail-loud `dis-pii` gate
before persistence, writes one metadata-only bronze row via `dis-rls` under the
object's tenant, and publishes `ingress.ready` carrying the pointer the streaming
consumer needs. It reads `trace_id` and identity from the object and never mints a
`trace_id`. A re-finalization of the same content for the same source payload and
tenant within the dedup window returns the prior `trace_id` and produces no second
bronze row and no second publish.

The worker is the Phase-2 half of CSV upload (D36): event-triggered, not request-
receiving; it has no caller to authenticate; identity is inherited from the upload
session via `resolve_from_upload`. It is permissive (D13): structural preflight
only, semantic validation is the streaming consumer's. It writes bronze before it
publishes (D5), so a lost publish is recoverable from bronze.

What it does not do: no mapping, no four sub-stages, no Pandera suites, no canonical
writes, no atomic dual-write, no `mapping_version_id`, no quarantine routing (all
Slice 10). No `trace_id` minting (Slice 8). No PII tokenizer, key vault, token
store, or per-column flag mechanism (the seam is wired fail-loud; the real posture
stays at D40's deadline). No tier-0 upload-endpoint validation (Slice 8, D51).

## Task

Build the worker in the directory the repo reserves for it; confirm the exact
placement and the current scaffolding state in plan mode rather than assuming it
(the D36 rename and the removal of any Phase-1 residue may not have landed, since
Slice 8 has not run). Decompose:

1. **Trigger intake.** Consume the GCS object-finalized notification for a finalized
   CSV object at the canonical path. Parse identity and `trace_id` from the object
   per the fixed path scheme (hard rule 9), and recover the `upload_id` needed for
   `resolve_from_upload` (open question 1). The existence and local delivery of this
   trigger is a plan-mode reconcile against the live stack (does fake-gcs-server emit
   finalize notifications into the Pub/Sub emulator, or is a shim or direct test
   invocation needed); the proof errors, never skips, if the mechanism is absent.

2. **Identity resolution.** Call `resolve_from_upload` to obtain the resolved
   internal identity (`tenant_id`, `store_id`) for the upload session, tested against
   the Slice 2 fake. The worker receives already-resolved internal identity and
   performs no external-to-internal id translation (open question 4 routes the D37
   determination: whether `resolve_from_upload` returns internal UUIDs, in which case
   D37 does not fire here, or an external identifier, in which case the worker is
   D37's deadline trigger).

3. **Structural preflight (DuckDB).** Does the object parse as CSV; plausible
   structure, row count, type sniff. Structural only (D13, D51): no column- or
   mapping-aware checks (those are the source-shape suite, Slice 10). Parse-as-CSV is
   mechanism the sniff needs, not a tier-0 policy claim borrowed from Slice 8. The
   preflight-failure outcome inside the permissive posture is named in plan mode
   (open question 6) without inventing a quarantine path.

4. **PII gate.** Pass recognized PII columns through the `dis-pii` fail-loud gate
   before any persistence (hard rule 2). Wire the existing Slice 4 seam; build no
   tokenizer, key vault, or flag mechanism. Under the live schema no authoritative
   per-column PII flag exists (D40 limitation 2), so the CSV-flag path is inert and
   only heuristic name detection can fire, with bounded coverage (D40 limitation 1).
   Register this state; do not widen detection here.

5. **Bronze write.** Write one metadata-only bronze row (pointer to the GCS object,
   identity, `trace_id`, and the dedup-key fields) via `dis-rls` under the object's
   tenant (hard rules 1 and 12). Derive the exact bronze columns and the dedup-key
   storage from the live schema in plan mode (open question 3); if a column the
   idempotency check or the publish needs is absent, surface and register the gap, do
   not edit DDL. Target safety: the write is DIS on 5433, never Customer Master on
   5432, asserted positively (Slice 7 pattern).

6. **`ingress.ready` publish.** Publish the frozen `ingress.ready` envelope (hard
   rule 10) carrying the pointer the streaming consumer needs, only after the bronze
   row lands (write-then-publish, D5). Derive the field set from the frozen contract
   and reconcile against what the worker can populate, in plan mode (open question 5);
   populate the contract, never change its shape.

7. **Idempotency.** The same content hash and source payload and tenant within the
   dedup window returns the prior `trace_id`, producing no second bronze row and no
   second publish. Derive the window mechanism and where the prior `trace_id` is
   looked up from the live schema and the frozen contract in plan mode (open question
   3). The key is a required value: a missing key raises a `dis-core` error, never a
   silent fallback (code-quality rule 4); the check errors, never skips, if its
   backing store is absent (Slice 4 and 7 lesson).

8. **Audit emission.** Emit a fire-and-forget audit event at each stage (preflight,
   bronze write, publish, and the idempotent no-op), each carrying `tenant_id` (D43),
   `trace_id`, and the load-bearing id (code-quality rule 5). Audit failures are
   logged, never raised, never block the data path (hard rule 11); duplicate audit
   rows are tolerated (D44). The D45 exposure (finite partition coverage, no DEFAULT
   partition, silent loss on out-of-range writes) is inherited operational risk
   surfaced here, not this slice's to fix.

## Acceptance criteria

1. The worker is importable and runs as a standalone Pub/Sub-subscribed worker. Its
   test directory is collected by the runner, with a check proving collection (an
   excluded dir reports green having run nothing, Slice 7 lesson). `make check` shows
   no tier regression and the new tests pass.
2. Given a finalized CSV object at the canonical path, the worker resolves identity
   via `resolve_from_upload` (against the Slice 2 fake) and reads `trace_id` from the
   object; a test confirms the emitted `trace_id` equals the one on the object and
   that the worker mints none. The GCS-finalize trigger proof errors, never skips,
   when the trigger mechanism is absent.
3. The DuckDB structural preflight accepts a well-formed CSV and produces a loud,
   typed failure on one that does not parse as CSV or fails the structural sniff; a
   test exercises both. That the preflight performs no column- or mapping-aware
   checks is a review-only property (a test cannot prove a feature's absence),
   confirmed by review with an import or scope check where one is expressible.
4. A recognized PII column routes through the `dis-pii` gate and raises fail-loud
   before any persistence; a test confirms the raise precedes the bronze write. That
   no tokenizer, backend, or flag mechanism is built and the CSV-flag path is inert
   is recorded; the not-raise branch is reachable only via an injected backend
   (tests), with no config default that disables the gate.
5. The bronze write lands one metadata-only row via `dis-rls` under the object's
   tenant. A positive target-safety assertion proves the connection is on the DIS
   database (5433), the read and parse run before any write, and a wrong target
   exits before writing (Slice 7 pattern). The write's RLS tenant scoping is set.
6. `ingress.ready` is published as the frozen envelope carrying the streaming
   consumer's pointer, only after the bronze row lands (write-then-publish, D5); a
   test confirms the ordering and that the published envelope matches the frozen
   contract's field set.
7. A re-finalization of the same content plus source payload plus tenant within the
   window returns the prior `trace_id` and produces no second bronze row and no
   second publish; a test exercises the duplicate path and asserts both the returned
   prior `trace_id` and the no-op on bronze and publish. The idempotency check
   errors, never skips, if its backing store is absent, and a missing required dedup
   key raises a `dis-core` error rather than falling back silently.
8. Audit events are emitted fire-and-forget at each stage, each carrying
   `tenant_id`, `trace_id`, and the load-bearing id; a test injects an audit failure
   and confirms the data path still completes (the failure is logged, not raised).
   Duplicate audit rows are tolerated.
9. The service raises `dis-core` errors (no raw `RuntimeError`/`ValueError`), binds
   `tenant_id`, `trace_id`, `service`, `stage` in logs, logs no PII or raw payloads,
   and mints any IDs via the `dis-core` `ids` helper (not `trace_id`, which it
   reads). The service `CLAUDE.md` records its new invariants before slice exit
   (under 100 lines).

## Scope boundary

In scope:
- The `csv-ingest-worker` service: trigger intake, identity resolution via
  `resolve_from_upload`, the DuckDB structural preflight, the wired `dis-pii`
  fail-loud gate, the `dis-rls` bronze write, the `ingress.ready` publish,
  idempotency, and per-stage fire-and-forget audit.
- The tests proving trigger intake, the preflight both ways, the PII raise-before-
  persist, the bronze-write target safety, write-then-publish ordering, the
  idempotent no-op, and audit fire-and-forget.
- The service `CLAUDE.md` invariants.

Out of scope (do not let the slice sprawl):
- The Phase-1 upload-session endpoint: session validation, `trace_id` minting,
  signed-URL issuance. That is Slice 8 in dis-ui-server (D36). The worker reads
  `trace_id`, never mints it.
- Tier-0 upload-endpoint structural validation (file present, non-empty, decodes,
  parses, min-rows floor). Assigned to dis-ui-server's endpoint (D51, Slice 8). The
  worker's preflight is its own post-landing structural sniff; parse-as-CSV is
  mechanism it needs, not tier-0 policy. The D51-versus-PUT-path tension (no server-
  side bytes at URL-issuance time on the signed-PUT path) is registered for Slice 8.
  *New scope; not absorbed here.*
- Mapping, the four sub-stages, Pandera suites, canonical writes, the atomic dual-
  write, `mapping_version_id` stamping, and quarantine routing. *Slice 10.*
- Enforcing the no-orphan rule (every record belongs to a tenant and a store). That
  invariant is already engine-enforced at the canonical write by the composite
  `(tenant_id, store_id)` FK to `identity_mirror.stores` (D39), which is Slice 10's
  write. The worker adds no constraint to guarantee it. Bronze is a coarser grain
  (one row per ingress chunk), so the worker's bronze `store_id` behaviour is a
  data-hygiene question (open question 3), not the orphan guarantee.
- Building a PII tokenizer, key vault, token store, or a per-column PII flag
  mechanism. The seam is wired fail-loud; the real one-way-versus-reversible posture
  and the flag mechanism stay at D40's deadline (the first PII-carrying receiver, or
  a CSV mapping that flags a PII column). *New scope plus a schema/DDL change; not
  here.*
- Resolving D37 (external `t_*`/`s_*` versus internal UUID keys). The worker takes
  resolved internal identity; if `resolve_from_upload` returns an external
  identifier the worker is D37's deadline trigger and the doc surfaces it, but the
  resolution is not built here. *OPEN.*
- Resolving D38 (event-table dedup columns absent) and D42 (audit duplicate-detail
  fields absent). The worker writes no canonical row and no duplicate-audit detail.
  *OPEN, Slice 10.*
- Authoring or changing any DDL. A bronze column the idempotency check or the
  publish needs but the schema lacks is surfaced and registered in plan mode, not
  added here.
- Audit partition management (D45). The finite-partition silent-loss gap is an
  operational task; the worker logs a swallowed audit failure as alert-worthy
  (Slice 6 mitigation), it creates no partitions. *OPEN.*
- The Pub/Sub consumer mode of Mirror Sync and the API/webhook, ERP CSV POST, and
  reverse-API receivers (all DEFERRED, each with its own trigger).
- Building worker surface later slices do not yet need (batching, retry tuning
  beyond what the trigger contract requires). Build to current need; later slices
  extend.

## Constraints

- The worker reads `trace_id` from the object and never mints one (hard rule 4); a
  re-finalization reuses the original via idempotency.
- All GCS access is via `dis-storage` on the fixed canonical path (hard rule 9); the
  worker improvises no paths.
- The bronze write goes through `dis-rls` with the tenant context set (hard rules 1
  and 12); no async DB call outside an RLS session. Target safety: DIS on 5433,
  never Customer Master on 5432, asserted positively.
- `ingress.ready` is a frozen contract (hard rule 10): populate it, never change its
  shape; a needed-but-absent field is surfaced and registered, not improvised.
- Bronze-first: the bronze row lands before the publish (D5), so a lost publish is
  recoverable.
- PII passes through the `dis-pii` gate before any persistence (hard rule 2); the
  gate fails loud, no config default disables it, and no real tokenization is built
  (D24, D40).
- Audit is fire-and-forget (hard rule 11): failures logged, never raised, never
  blocking; duplicates tolerated (D44); every event carries `tenant_id` (D43),
  `trace_id`, and the load-bearing id (code-quality rule 5).
- Idempotency keys are required values: a missing key raises a `dis-core` error,
  never a silent fallback (code-quality rule 4). The idempotency check and the GCS-
  finalize trigger proof error rather than skip when their backing store or mechanism
  is absent (Slice 4 and 7 lesson).
- No silent fallbacks; no swallowed exceptions except audit (code-quality rules 4 and
  6); one concern per function (rule 7).
- DuckDB is a new pinned dependency here. If the preflight relies on a specific
  DuckDB CSV-parse or type-sniff behaviour a version bump could change, contain it
  and let a canary assert the relied-on behaviour, not a version string (Slice 5
  pinned-dependency pattern). Confirm in plan mode whether this applies (open
  question 8).
- Errors from `dis-core/errors.py`; UUIDv7 via the `dis-core` helper; structured
  logging binds the four keys; never log PII or raw payloads.
- Live contact is the local stack (Postgres on 5433, the GCS and Pub/Sub emulators,
  the Slice 2 fakes); plan-mode introspection is read-only against `ithina_dis_db`
  on 5433.
- Service `CLAUDE.md` under 100 lines; new invariants captured before slice exit.

## Open questions (for plan mode to resolve)

1. The path-scheme versus `upload_id` reconcile. Hard rule 9 fixes the GCS path as
   carrying `trace_id`; D36 says `resolve_from_upload(upload_id)` is called with the
   upload session id "encoded in the object path." Confirm against the live
   `dis-storage` path scheme and the Identity contract how the worker recovers the
   `upload_id` (a distinct path segment, the `trace_id` itself serving as the key, or
   an object-metadata field). If neither the path nor the contract carries a
   recoverable `upload_id`, that is a path-scheme or contract gap to register, not to
   fix here.
2. The trigger mechanism and its source. Two parts.
   (a) *Local delivery.* Introspect the live stack: does fake-gcs-server emit
   object-finalized notifications into the Pub/Sub emulator, and on which topic, or
   must the worker be driven by a shim or direct test invocation. This decides
   whether the worker is exercisable end-to-end locally without Slice 8. The proof
   errors, never skips, if the mechanism is absent. Do not build cloud notification
   wiring (a deferred infra trigger).
   (b) *The trigger topic is not one of the six frozen DIS topics.* `ingress.ready`
   is what the worker publishes downstream; the finalize notification is a separate
   inbound channel that starts the worker. So there is either a seventh plumbing
   topic for GCS notifications or different local wiring. Confirm which exists;
   register a reserved-topic gap if neither is present.
   (c) *Trigger-source fork (architecture-level, raise before building).* Should the
   landed-CSV signal come from GCS firing `OBJECT_FINALIZE` into a notification
   topic, or from dis-ui-server publishing an explicit trigger after the PUT? The
   tension: dis-ui-server only issues the signed URL and is then out of the loop, so
   it does not know when the browser's PUT actually completes; only GCS observes the
   landing. A dis-ui-server-published trigger would therefore need a client callback
   or a poll, both of which trust the client. GCS-fired finalize is the native event
   and matches D36's "event-triggered, no caller to authenticate." If the answer is
   GCS, the worker must filter on the upload path prefix so it fires only on upload
   objects, not on every object finalize in the bucket (see open question 9). This
   choice touches D36 and architecture.md; per CLAUDE.md it is raised in this chat
   and settled in `decisions.md` before code, not decided mid-build by the worker.
3. Bronze columns and the idempotency key. Introspect the live bronze schema: the
   metadata-only row's columns, whether a source payload id, a content hash, and a
   dedup-window timestamp exist as queryable columns, and where the prior `trace_id`
   is looked up for the idempotent return. If the idempotency key or the
   `ingress.ready` pointer needs a column the schema lacks, register the gap (a
   bronze analog of D38); do not edit DDL. Confirm the bronze table's RLS posture so
   the write's tenant scoping is correct. Three specifics to settle here:
   (a) *`store_id` at the bronze grain.* Confirm the live `bronze.store_id`
   nullability and whether an upload session binds exactly one store. If the session
   is single-store, the worker knows `store_id` at bronze-write time and populates
   it; if store is per-row inside the chunk, a chunk-level `store_id` is legitimately
   NULL and per-row store binding happens at the canonical write (Slice 10). If the
   session is single-store and the column ought to be NOT NULL on bronze, that is a
   DDL-tightening gap: register it with its own D-number for a future migration
   slice; do not alter DDL here. The smoke rows showing NULL `store_id` predate the
   worker (their `source_id` reads as the literal `manual_csv_upload`, not a resolved
   id) and do not define the contract.
   (b) *`source_payload_id` provenance.* The idempotency key names a source payload
   id distinct from `source_id` (the channel). Pin what it is for a CSV upload (the
   upload session id, a value on the object path, or object metadata) and where the
   worker reads it. Interacts with open questions 1 and 5.
   (c) *The dedup-window clock.* Name which timestamp the 24h window is measured
   against (object finalize time, worker processing wall-clock, or the upload session
   time), since replay and late delivery make these diverge.
4. `resolve_from_upload`'s return shape and D37. Confirm whether the Identity
   contract's `resolve_from_upload` returns internal UUID identity (then D37 does not
   fire here and the worker takes resolved internal identity) or an external
   `t_*`/`s_*` identifier (then the worker is D37's stated deadline trigger and that
   must be surfaced before identity is resolved). Do not settle D37 here; route the
   determination.
5. The `ingress.ready` field set. Derive the frozen envelope's fields from the
   contract and reconcile against what the worker can populate from the object, the
   resolved identity, and the bronze row. Name any field the worker cannot populate
   without resolving something it does not yet have (interacts with question 4 if a
   source identifier is external). Populate the frozen contract; surface a mismatch
   rather than changing the shape.
6. Preflight-failure outcome within permissive ingress. A structurally failed object
   writes no canonical and no quarantine (Slice 10). Decide what the worker does on
   preflight failure inside the D13 permissive posture: audit-only and stop, or a
   Phase-2 failure signal, without inventing a quarantine path. Keep it structural;
   column- or mapping-aware failure is the source-shape suite's (Slice 10).
7. Service scaffolding state. Confirm the live directory: whether the D36 rename
   (`receiver-csv-upload` to `csv-ingest-worker`) and the removal of any Phase-1
   handler residue have landed, given Slice 8 has not run. Read the actual tree; do
   not assume the rename is done. Place code in the reserved dirs; invent none.
8. DuckDB pinned-behaviour canary. Determine whether the preflight relies on a
   specific DuckDB CSV-parse or type-sniff behaviour a version bump could change. If
   so, contain it and add a canary asserting the relied-on behaviour (not a version
   string), per the Slice 5 pattern. If the reliance is not behaviour-sensitive, say
   so and skip the canary.
9. Trigger scoping by path. If the trigger is GCS object-finalize (open question 2c),
   the worker fires on object landings in the bucket, which can include objects the
   worker must not process (non-upload artifacts, bronze payloads, partial or aborted
   writes). Confirm how the worker filters to only the upload path prefix, so a
   finalize on an unrelated object is ignored rather than processed. Keep the filter
   in the trigger intake, not a downstream check.
10. Identity authority versus the trigger payload. Identity is resolved via
    `resolve_from_upload` regardless of trigger source; the trigger message (whoever
    sends it) is not trusted as the identity authority. A dis-ui-server-published
    trigger could carry `tenant_id`/`store_id` on the message, but the worker still
    resolves from the upload session and does not take the message's identity claim
    on trust. Confirm this stance holds in plan mode so no shortcut reads identity off
    the trigger.
11. At-least-once trigger delivery. Both GCS notifications and Pub/Sub redeliver, so
    the worker can receive the same finalize more than once. Confirm the idempotency
    path (criterion 7) is what absorbs redelivery (same content, source payload, and
    tenant returns the prior `trace_id`, no second bronze row, no second publish), so
    a redelivered trigger is a no-op, not a duplicate ingest.
