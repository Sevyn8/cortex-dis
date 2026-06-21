# Slice 9b: CSV upload, Phase 2 worker (csv-ingest-worker)

## Depends on

- Slice 9a (identity correction), which is built and pushed. It is the reason this
  doc differs from its earlier form: identity is now the internal UUID, the external
  codes ride along as optional fields, the GCS path tenant segment is the UUID, and
  `dis-storage` exposes `parse_object_path` (D52, D53). The worker consumes those.
- Slice 8 (CSV upload Phase 1) only by contract, not by code. The single contract
  between the two halves is the `csv.received` event: dis-ui-server publishes it
  after the upload's PUT is confirmed saved in GCS, carrying the already-resolved
  internal identity (UUID), the external codes, `trace_id`, the upload session id,
  and the GCS pointer (D54); the exact field set is the `csv.received` contract
  authored in 9a, confirmed against that file in plan mode (open question 1). Slice 8
  is not built (deferred pending the dis-ui-server
  demand list), so this slice does not depend on Slice 8 code: tests publish a
  `csv.received` event and seed the finalized object directly, and the worker runs
  against them. The worker trusts the event's identity and reads `trace_id` from it;
  it never resolves identity and never mints a `trace_id`.
- Slice 4 for `dis-storage` (GCS object access and the canonical path scheme, hard
  rule 9, now with the UUID tenant segment and `parse_object_path` from 9a),
  `dis-rls` (the RLS-aware session for the bronze write, hard rules 1 and 12), and
  `dis-pii` (the Slice 4 fail-loud gate, wired here, not extended).
- Slice 6 for `dis-audit` (fire-and-forget audit emission to Cloud SQL
  `audit.events`, hard rule 11).
- Slice 3 for `dis-core` (`errors` root, `ids` for any minted UUIDs, `trace_id` read
  and propagation, `timestamps`, structured `logging`).
- Slice 2 for the fixture seeder (writes test tenants/stores into `identity_mirror`
  so the bronze write's identity FK resolves) and the test publishers, run in tests;
  no real Customer Master and no Identity Service are touched.
- Decisions this slice must honour: D54 (`csv.received` is the trigger; the worker
  trusts the event's resolved identity, reads not mints `trace_id`, and calls no
  Identity Service); D36 (Phase 2 is a standalone, event-triggered worker, no caller
  to authenticate); D5 (bronze-first: write then publish, so a lost publish is
  recoverable); D13 (permissive ingress: structural preflight only, semantic
  validation is the streaming consumer's); D24 and D40 (PII tokenization seam,
  fail-loud, no backend in v1.0; the CSV-flag path is inert because no authoritative
  per-column flag exists in the live schema, D40 limitation 2); D43 (every audit
  event carries a known `tenant_id`; identity is on the event, so this holds from the
  first stage); D44 (audit duplicates are tolerated in Cloud SQL); D16 (DuckDB used
  for preflight); D52/D53 (UUID identity, optional codes, UUID path segment).
- CLAUDE.md hard rules: 4 (`trace_id` read, never invented mid-pipeline), 9 (GCS via
  `dis-storage` only, paths built and parsed only there), 1 and 12 (bronze via
  `dis-rls` with tenant context), 10 (`csv.received` and `ingress.ready` are frozen
  contracts: populate, never change), 11 (audit fire-and-forget). Code-quality rules:
  3 (tests in the same commit), 4 (no silent fallbacks for required values), 5
  (errors carry `tenant_id`, `trace_id`, and the load-bearing id), 6 (no swallowed
  exceptions except audit), 7 (one concern per function).
- Downstream consumer: the streaming consumer (Slice 10) reads `ingress.ready` and
  the bronze pointer and owns mapping, validation, canonical writes,
  `mapping_version_id`, and failure routing. It sizes what the publish must carry.
  The worker does none of that.

## Goal

After this slice, a standalone worker service exists that turns a `csv.received`
event into a bronze record and an `ingress.ready` notification, idempotently, with
audit at each step. When dis-ui-server publishes `csv.received` (in tests, when a test
publishes it), the worker trusts the event's resolved internal identity (`tenant_id`,
`store_id` as UUIDs), reads `trace_id` from the event, opens the finalized CSV object
at the GCS pointer the event carries (the event's exact field set is the 9a
`csv.received` contract, confirmed in plan mode), runs a DuckDB structural preflight (does the
object parse as CSV; plausible structure, row count, type sniff), passes any
recognized PII through the fail-loud `dis-pii` gate before persistence, writes one
metadata-only bronze row via `dis-rls` under the event's tenant, and publishes
`ingress.ready` carrying the pointer the streaming consumer needs. It reads identity
and `trace_id` from the event and never resolves identity and never mints a
`trace_id`. A redelivery of the same content for the same source payload and tenant
within the dedup window returns the prior `trace_id` and produces no second bronze
row and no second publish.

The worker is the Phase-2 half of CSV upload (D36): event-triggered by `csv.received`
(D54), not request-receiving; it has no caller to authenticate; identity is carried
on the event, already resolved by Phase 1. It calls no Identity Service: the trust
boundary is the event (D54). It is permissive (D13): structural preflight only,
semantic validation is the streaming consumer's. It writes bronze before it publishes
(D5), so a lost publish is recoverable from bronze.

What it does not do: no identity resolution, no Identity Service call, no
external-to-internal id translation (the event carries resolved UUID identity, D54).
No `trace_id` minting (it reads `trace_id` off the event; Phase 1 / Slice 8 mints it).
No mapping, no four sub-stages, no Pandera suites, no canonical writes, no atomic
dual-write, no `mapping_version_id`, no quarantine routing (all Slice 10). No PII
tokenizer, key vault, token store, or per-column flag mechanism (the seam is wired
fail-loud; the real posture stays at D40's deadline). No tier-0 upload-endpoint
validation (Slice 8, D51). It does not publish `csv.received` (that is dis-ui-server,
Slice 8); it only consumes it.

## Task

Build the worker in the directory the repo reserves for it; confirm the exact
placement and the current scaffolding state in plan mode rather than assuming it (the
D36 rename and the removal of any Phase-1 residue may not have landed, since Slice 8
has not run). Decompose:

1. **Trigger intake (`csv.received`).** Subscribe to the `csv.received` topic and
   consume the event (D54). The event carries the resolved internal identity (UUID
   `tenant_id`/`store_id`), the external codes, `trace_id`, the upload session id, and
   the GCS pointer. Read identity and `trace_id` from the event; do not resolve, do
   not mint. Parse the GCS pointer's path with `dis-storage`'s `parse_object_path` (9a,
   D53) to recover the path's components and cross-check them against the event's
   identity (open question 1). The local delivery of this trigger is a plan-mode
   reconcile against the live stack (the `csv.received` topic exists on the emulator
   after 9a created it; confirm a test can publish to it and the worker receive); the
   proof errors, never skips, if the mechanism is absent. Do not build cloud
   notification wiring (a deferred infra trigger).

2. **Structural preflight (DuckDB).** Open the finalized object at the event's GCS
   pointer (via `dis-storage`) and run the preflight: does the object parse as CSV;
   plausible structure, row count, type sniff. Structural only (D13, D51): no column-
   or mapping-aware checks (those are the source-shape suite, Slice 10). Parse-as-CSV
   is mechanism the sniff needs, not a tier-0 policy claim borrowed from Slice 8. The
   preflight-failure outcome inside the permissive posture is named in plan mode (open
   question 5) without inventing a quarantine path.

3. **PII gate.** Pass recognized PII columns through the `dis-pii` fail-loud gate
   before any persistence (hard rule 2). Wire the existing Slice 4 seam; build no
   tokenizer, key vault, or flag mechanism. Under the live schema no authoritative
   per-column PII flag exists (D40 limitation 2), so the CSV-flag path is inert and
   only heuristic name detection can fire, with bounded coverage (D40 limitation 1).
   Register this state; do not widen detection here.

4. **Bronze write.** Write one metadata-only bronze row (pointer to the GCS object,
   the event's UUID identity, `trace_id`, and the dedup-key fields) via `dis-rls`
   under the event's tenant (hard rules 1 and 12). Derive the exact bronze columns and
   the dedup-key storage from the live schema in plan mode (open question 2); if a
   column the idempotency check or the publish needs is absent, surface and register
   the gap, do not edit DDL. Target safety: the write is DIS on 5433, never Customer
   Master on 5432, asserted positively (Slice 7 pattern).

5. **`ingress.ready` publish.** Publish the frozen `ingress.ready` envelope (hard rule
   10) carrying the pointer the streaming consumer needs, only after the bronze row
   lands (write-then-publish, D5). The envelope's identity fields are the event's UUID
   identity, with the external codes populated as the optional fields (D52, producer-
   required when publishing). Derive the field set from the frozen contract and
   reconcile against what the worker can populate, in plan mode (open question 3);
   populate the contract, never change its shape.

6. **Idempotency.** The same content hash and source payload and tenant within the
   dedup window returns the prior `trace_id`, producing no second bronze row and no
   second publish. The source payload id is the upload session id carried on the event
   (D54: the session id stays on the event for idempotency and lineage, not identity).
   Derive the window mechanism and where the prior `trace_id` is looked up from the
   live schema and the frozen contract in plan mode (open question 2). The key is a
   required value: a missing key raises a `dis-core` error, never a silent fallback
   (code-quality rule 4); the check errors, never skips, if its backing store is
   absent (Slice 4 and 7 lesson).

7. **Audit emission.** Emit a fire-and-forget audit event at each stage (preflight,
   bronze write, publish, and the idempotent no-op), each carrying `tenant_id` (D43,
   known from the event from the first stage), `trace_id`, and the load-bearing id
   (code-quality rule 5). Audit failures are logged, never raised, never block the
   data path (hard rule 11); duplicate audit rows are tolerated (D44). The D45 exposure
   (finite partition coverage, no DEFAULT partition, silent loss on out-of-range
   writes) is inherited operational risk surfaced here, not this slice's to fix.

## Acceptance criteria

1. The worker is importable and runs as a standalone Pub/Sub-subscribed worker on the
   `csv.received` topic. Its test directory is collected by the runner, with a check
   proving collection (an excluded dir reports green having run nothing, Slice 7
   lesson). `make check` shows no tier regression and the new tests pass.
2. Given a `csv.received` event and a finalized CSV object at the event's pointer, the
   worker takes identity and `trace_id` from the event (a test confirms the emitted
   `trace_id` equals the event's and that the worker mints none, and that it makes no
   Identity Service call), and `parse_object_path` recovers the path components which
   match the event's identity. The `csv.received` delivery proof errors, never skips,
   when the mechanism is absent.
3. The DuckDB structural preflight accepts a well-formed CSV and produces a loud,
   typed failure on one that does not parse as CSV or fails the structural sniff; a
   test exercises both. That the preflight performs no column- or mapping-aware checks
   is a review-only property (a test cannot prove a feature's absence), confirmed by
   review with an import or scope check where one is expressible.
4. A recognized PII column routes through the `dis-pii` gate and raises fail-loud
   before any persistence; a test confirms the raise precedes the bronze write. That
   no tokenizer, backend, or flag mechanism is built and the CSV-flag path is inert is
   recorded; the not-raise branch is reachable only via an injected backend (tests),
   with no config default that disables the gate.
5. The bronze write lands one metadata-only row via `dis-rls` under the event's
   tenant. A positive target-safety assertion proves the connection is on the DIS
   database (5433), the read and parse run before any write, and a wrong target exits
   before writing (Slice 7 pattern). The write's RLS tenant scoping is set from the
   event's UUID tenant.
6. `ingress.ready` is published as the frozen envelope carrying the streaming
   consumer's pointer and the event's UUID identity (with codes in the optional
   fields), only after the bronze row lands (write-then-publish, D5); a test confirms
   the ordering and that the published envelope matches the frozen contract's field
   set.
7. A redelivery of the same content plus source payload plus tenant within the window
   returns the prior `trace_id` and produces no second bronze row; **no second publish
   if the prior ingest was published (or FAILED); the publish is completed and marked
   if the prior is an unpublished RECEIVED row** (resume-and-mark — a crash between
   the bronze write and the publish must converge on redelivery, not stall; refined
   in plan mode, recorded as decisions.md D59). Tests exercise the duplicate path
   both ways: the published-prior full no-op asserting the returned prior `trace_id`
   and the no-op on bronze and publish, and the unpublished-prior resume asserting
   exactly one completing publish under the prior `trace_id` plus the publish mark.
   The idempotency check errors, never skips, if its backing store is absent, and a
   missing required dedup key raises a `dis-core` error rather than falling back
   silently.
8. Audit events are emitted fire-and-forget at each stage, each carrying `tenant_id`,
   `trace_id`, and the load-bearing id; a test injects an audit failure and confirms
   the data path still completes (the failure is logged, not raised). Duplicate audit
   rows are tolerated.
9. The service raises `dis-core` errors (no raw `RuntimeError`/`ValueError`), binds
   `tenant_id`, `trace_id`, `service`, `stage` in logs, logs no PII or raw payloads,
   and mints any IDs via the `dis-core` `ids` helper (not `trace_id`, which it reads).
   It passes `mypy --strict` under the 9d gate. The service `CLAUDE.md` records its new
   invariants before slice exit (under 100 lines).

## Scope boundary

In scope:
- The `csv-ingest-worker` service: `csv.received` trigger intake, the DuckDB
  structural preflight, the wired `dis-pii` fail-loud gate, the `dis-rls` bronze
  write, the `ingress.ready` publish, idempotency, and per-stage fire-and-forget
  audit.
- The tests proving trigger intake, the preflight both ways, the PII raise-before-
  persist, the bronze-write target safety, write-then-publish ordering, the idempotent
  no-op, and audit fire-and-forget.
- The service `CLAUDE.md` invariants.

Out of scope (do not let the slice sprawl):
- Identity resolution and any Identity Service call. The event carries resolved UUID
  identity; the worker trusts it (D54). The worker performs no resolve, no external-
  to-internal translation. Building or changing the Identity Service is Slice 13.
- Publishing `csv.received` and the mechanism by which dis-ui-server learns the PUT
  completed. That is Slice 8 in dis-ui-server (D54). The worker only consumes the
  event.
- The Phase-1 upload-session endpoint: session validation, `trace_id` minting,
  signed-URL issuance, identity resolution to UUID + codes. That is Slice 8 (D36,
  D54). The worker reads `trace_id` and identity off the event, never mints or
  resolves.
- Tier-0 upload-endpoint structural validation (file present, non-empty, decodes,
  parses, min-rows floor). Assigned to dis-ui-server's endpoint (D51, Slice 8). The
  worker's preflight is its own post-landing structural sniff; parse-as-CSV is
  mechanism it needs, not tier-0 policy.
- Mapping, the four sub-stages, Pandera suites, canonical writes, the atomic dual-
  write, `mapping_version_id` stamping, and quarantine routing. *Slice 10.*
- Enforcing the no-orphan rule (every record belongs to a tenant and a store). That
  invariant is engine-enforced at the canonical write by the composite
  `(tenant_id, store_id)` FK to `identity_mirror.stores` (D39), which is Slice 10's
  write. The worker adds no constraint to guarantee it. Bronze is a coarser grain (one
  row per ingress chunk), so the worker's bronze `store_id` behaviour is a data-
  hygiene question (open question 2), not the orphan guarantee.
- Building a PII tokenizer, key vault, token store, or a per-column PII flag
  mechanism. The seam is wired fail-loud; the real one-way-versus-reversible posture
  and the flag mechanism stay at D40's deadline (the first PII-carrying receiver, or a
  CSV mapping that flags a PII column). *New scope plus a schema/DDL change; not here.*
- Resolving D38 (event-table dedup columns absent) and D42 (audit duplicate-detail
  fields absent). The worker writes no canonical row and no duplicate-audit detail.
  *OPEN, Slice 10.*
- Authoring or changing any DDL. A bronze column the idempotency check or the publish
  needs but the schema lacks is surfaced and registered in plan mode, not added here.
- Audit partition management (D45). The finite-partition silent-loss gap is an
  operational task; the worker logs a swallowed audit failure as alert-worthy (Slice 6
  mitigation), it creates no partitions. *OPEN.*
- The Pub/Sub consumer mode of Mirror Sync and the API/webhook, ERP CSV POST, and
  reverse-API receivers (all DEFERRED, each with its own trigger).
- Building worker surface later slices do not yet need (batching, retry tuning beyond
  what the trigger contract requires). Build to current need; later slices extend.

## Constraints

- The worker reads identity and `trace_id` from the `csv.received` event and never
  resolves identity or mints a `trace_id` (D54, hard rule 4); a redelivery reuses the
  original `trace_id` via idempotency.
- The event is the trust boundary (D54): the worker takes the event's resolved
  identity on trust and does not re-verify it against the Identity Service or
  Customer Master. It does cross-check the GCS path's parsed tenant segment against
  the event's tenant (open question 1) as a consistency check, not a re-resolution.
- All GCS access is via `dis-storage` (hard rule 9); paths are parsed via
  `parse_object_path` (9a), never improvised or hand-split.
- The bronze write goes through `dis-rls` with the tenant context set from the event's
  UUID tenant (hard rules 1 and 12); no async DB call outside an RLS session. Target
  safety: DIS on 5433, never Customer Master on 5432, asserted positively.
- `csv.received` (consumed) and `ingress.ready` (published) are frozen contracts (hard
  rule 10): read and populate them, never change their shape; a needed-but-absent
  field is surfaced and registered, not improvised. The `ingress.ready` optional code
  fields are producer-required when the worker publishes (D52).
- Bronze-first: the bronze row lands before the publish (D5), so a lost publish is
  recoverable.
- PII passes through the `dis-pii` gate before any persistence (hard rule 2); the gate
  fails loud, no config default disables it, and no real tokenization is built (D24,
  D40).
- Audit is fire-and-forget (hard rule 11): failures logged, never raised, never
  blocking; duplicates tolerated (D44); every event carries `tenant_id` (D43, on the
  event from the first stage), `trace_id`, and the load-bearing id (code-quality rule
  5).
- Idempotency keys are required values: a missing key raises a `dis-core` error, never
  a silent fallback (code-quality rule 4). The idempotency check and the
  `csv.received` delivery proof error rather than skip when their backing store or
  mechanism is absent (Slice 4 and 7 lesson).
- DuckDB is a new pinned dependency here. If the preflight relies on a specific DuckDB
  CSV-parse or type-sniff behaviour a version bump could change, contain it and let a
  canary assert the relied-on behaviour, not a version string (Slice 5 pinned-
  dependency pattern). Confirm in plan mode whether this applies (open question 6).
- Errors from `dis-core/errors.py`; UUIDv7 via the `dis-core` helper; structured
  logging binds the four keys; never log PII or raw payloads. Code passes the
  `mypy --strict` gate (9d).
- Live contact is the local stack (Postgres on 5433, the GCS and Pub/Sub emulators,
  the Slice 2 fakes and seeder); plan-mode introspection is read-only against
  `ithina_dis_db` on 5433.
- Service `CLAUDE.md` under 100 lines; new invariants captured before slice exit.

## Open questions (for plan mode to resolve)

1. The `csv.received` payload versus the parsed path. Derive the frozen
   `csv.received` field set from the contract (added in 9a) and confirm which fields
   the worker reads: the UUID identity, the codes, `trace_id`, the upload session id,
   and the GCS pointer. Confirm `parse_object_path` (9a) recovers the path's tenant
   segment as a UUID and that it equals the event's `tenant_id`; decide what the worker
   does on a mismatch (a loud `dis-core` error, since the event is the trust boundary
   but an inconsistent path means a malformed producer). Do not re-resolve identity;
   this is a consistency cross-check only.
2. Bronze columns and the idempotency key. Introspect the live bronze schema: the
   metadata-only row's columns, whether a source payload id, a content hash, and a
   dedup-window timestamp exist as queryable columns, and where the prior `trace_id`
   is looked up for the idempotent return. If the idempotency key or the
   `ingress.ready` pointer needs a column the schema lacks, register the gap (a bronze
   analog of D38); do not edit DDL. Confirm the bronze table's RLS posture so the
   write's tenant scoping is correct. Three specifics to settle here:
   (a) *`store_id` at the bronze grain.* Confirm the live `bronze.store_id`
   nullability and whether an upload session binds exactly one store. The event
   carries a `store_id` (UUID) resolved by Phase 1; if the session is single-store the
   worker populates bronze `store_id` from the event; if store is per-row inside the
   chunk, a chunk-level `store_id` is legitimately NULL and per-row store binding
   happens at the canonical write (Slice 10). If the session is single-store and the
   column ought to be NOT NULL on bronze, that is a DDL-tightening gap: register it
   with its own D-number for a future migration slice; do not alter DDL here. The
   smoke rows showing NULL `store_id` predate the worker (their `source_id` reads as
   the literal `manual_csv_upload`, not a resolved id) and do not define the contract.
   (b) *`source_payload_id` provenance.* The idempotency key names a source payload id
   distinct from `source_id` (the channel). Per D54 it is the upload session id carried
   on the `csv.received` event; confirm the live bronze column that stores it and that
   the event field maps to it cleanly.
   (c) *The dedup-window clock.* Name which timestamp the 24h window is measured
   against (the `csv.received` publish/event time, worker processing wall-clock, or the
   upload session time), since redelivery and late delivery make these diverge.
3. The `ingress.ready` field set. Derive the frozen envelope's fields from the
   contract (corrected in 9a) and reconcile against what the worker can populate from
   the event, the parsed path, and the bronze row. Confirm the identity fields are the
   event's UUIDs and the optional code fields are populated from the event's codes
   (producer-required, D52). Name any field the worker cannot populate; populate the
   frozen contract, surface a mismatch rather than changing the shape.
4. Preflight-failure outcome within permissive ingress. A structurally failed object
   writes no canonical and no quarantine (Slice 10). Decide what the worker does on
   preflight failure inside the D13 permissive posture: audit-only and stop, or a
   Phase-2 failure signal, without inventing a quarantine path. Keep it structural;
   column- or mapping-aware failure is the source-shape suite's (Slice 10).
5. Service scaffolding state. Confirm the live directory: whether the D36 rename
   (`receiver-csv-upload` to `csv-ingest-worker`) and the removal of any Phase-1
   handler residue have landed, given Slice 8 has not run. Read the actual tree; do not
   assume the rename is done. Place code in the reserved dirs; invent none.
6. DuckDB pinned-behaviour canary. Determine whether the preflight relies on a
   specific DuckDB CSV-parse or type-sniff behaviour a version bump could change. If
   so, contain it and add a canary asserting the relied-on behaviour (not a version
   string), per the Slice 5 pattern. If the reliance is not behaviour-sensitive, say so
   and skip the canary.
7. At-least-once delivery. Pub/Sub redelivers, so the worker can receive the same
   `csv.received` event more than once. Confirm the idempotency path (criterion 7) is
   what absorbs redelivery (same content, source payload, and tenant returns the prior
   `trace_id`, no second bronze row, no second publish), so a redelivered event is a
   no-op, not a duplicate ingest.
8. The `csv.received` subscription wiring. Confirm how the worker subscribes on the
   local Pub/Sub emulator (the topic exists from 9a; confirm a subscription is created
   and how a test publishes a `csv.received` event to drive the worker end-to-end
   without Slice 8). Keep subscription creation in the worker's local plumbing or the
   test harness as appropriate; do not build cloud wiring. The delivery proof errors,
   never skips, if the topic or subscription is absent.
