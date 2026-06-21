# Slice 11a: quarantine the storm class (consumer writes held failures directly, then acks)

Stop the storm at its source and make held failures legible. Today a deterministic failure (one
that fails the same way every retry, the empty-ACTIVE-mapping that caused the local storm) is
nacked, so Pub/Sub redelivers it forever, recording the same failure endlessly and never setting
it aside. This slice makes the streaming-consumer recognize a known-deterministic failure, write
it to the quarantine store (which already exists) DIRECTLY, emit a QUARANTINED audit row, and ACK
the message so it leaves the queue, breaking the loop.

## Relationship to the original Slice 11 (now 11a / 11b)

The build-guide's Slice 11 was defined as a topic-mediated design: failing rows flow to the
`quarantine` Pub/Sub topic, and a separate drainer service consumes that topic and writes the
tables. This slice (11a) instead has the consumer write the quarantine store DIRECTLY and ack,
the simpler, fastest path to breaking the storm at its source (urgent, pre-deploy). The
topic-mediated decoupling (the `quarantine` topic + a drainer service, which also naturally
processes the Pub/Sub dead-letter contents) becomes Slice 11b, a later slice. 11a is informed by
what the storm taught: acking-at-source is the loop-breaker. The `quarantine` frozen contract and
the drainer service are NOT built here; they are 11b. This is a deliberate architecture choice
(operator-decided), not a plan-mode call.

The quarantine tables already exist (quarantine.quarantined_chunks, quarantine.quarantined_rows,
both with the full failure-audit correlation and a NEW/RESOLVED/DISMISSED lifecycle). This slice
does NOT create them. It builds the write path into them and wires the consumer to use it. It
consumes the failure-audit shape (D78) as the correlation seam and emits the QUARANTINED stage the
audit work reserved but never emitted.

## The model (one line)

Audit records what happened (write-only log); quarantine holds what failed so it can be recovered
(acted-on store). One deterministic failure produces both: a QUARANTINED audit row (the record)
and a quarantine store entry (the held thing). They correlate by trace_id + data_ingress_event_id.

## Scope

In:
- A new lib, dis-quarantine (parallel to dis-audit): the write functions and record shapes for
  the two existing tables. hold_chunk(...) writes a quarantined_chunks row; hold_row(...) (or a
  batch form) writes quarantined_rows rows. The lib owns the INSERT, the model, and the mapping
  from a failure to a stored record. Written via dis-rls rls_session (tenant-scoped, the audit
  posture). The lib is the reusable seam so the worker can use it later without duplication.
- Consumer wiring (streaming-consumer, orchestrate.py): on a NARROW ALLOWLIST of known
  deterministic failures, the catch path writes to quarantine and ACKS (instead of nacking, which
  loops). Chunk-level deterministic failures (the mapping-config class) -> quarantined_chunks;
  row-level validation failures -> quarantined_rows. Everything NOT on the allowlist keeps today's
  behavior (nack), with the Pub/Sub dead-letter policy as the backstop.
- The QUARANTINED audit emit: the audit Stage enum reserves QUARANTINED with no emitter; this
  slice emits it, carrying the failure-audit correlation shape (trace_id, tenant_id,
  data_ingress_event_id, mapping_version_id where known, the stable failure_code, the stage that
  failed), so the trail shows the disposition (failed at stage X -> quarantined) and Amit's
  Quarantine screen reads real data.

Out (deferred, named so they are not lost):
- The DLQ drainer service and the topic-mediated design (the `quarantine` topic + a drainer that
  reads dead-letter topics and lands their contents in quarantine). This is Slice 11b. The Pub/Sub
  dead-letter policy is the deploy backstop; the drainer that processes its contents, and the
  topic-mediated decoupling the original Slice 11 described, are 11b.
- Replay / recovery (taking a held entry, fixing the cause, resubmitting it, linking the replay by
  prior_trace_id). The store has the lifecycle columns (status, resolved_*) but this slice only
  writes status=NEW; transitions and replay are a later slice, frontend-coordinated with Amit's
  screen.
- Broad failure classification (deciding retryable-vs-deterministic for the whole FailureCode
  enum). This slice uses a narrow allowlist; widening is a future change.
- Transient-retry tuning (backoff/cap at the app level beyond what Pub/Sub's retry policy does).
- The worker's own quarantine wiring (the worker has deterministic failures too, e.g.
  path-mismatch; it can use the dis-quarantine lib later, not this slice).
- No table creation (the tables exist), no schema change unless grounding finds a genuine gap
  (flag, do not add speculatively).

## The narrow allowlist (what gets quarantined this slice)

Quarantine only failures that are KNOWN to fail identically on retry, so acking-instead-of-nacking
is correct (retrying cannot help). The starting set, to be confirmed against the FailureCode enum
in plan mode:
- MAPPING_CONFIG_INVALID (the storm's exact cause: empty/invalid ACTIVE mapping) -> chunk.
- The obvious deterministic siblings that cannot succeed on retry: CONTRACT_VIOLATION,
  SUITE_REF_UNSUPPORTED, HOT_POSITION_MISSING (the D63 miss) -> chunk. (Confirm each is truly
  deterministic, not transient-looking.)
- Row-level validation failures (VALIDATION_ROW_FAILED) -> rows. These are deterministic by nature
  (the data is malformed; re-running produces the same result).

Everything else (infra/transient failures, INFRA_FAILURE, anything not on the list) keeps today's
nack behavior. The Pub/Sub dead-letter policy backstops anything misjudged.

## A required grounding (the row-failure path)

Before wiring row-level quarantine, plan mode must establish how the consumer handles ROW-scoped
validation failures TODAY: does a chunk with some bad rows partial-succeed (good rows written to
canonical, bad rows logged), fully fail, or something else? The two-table store implies good rows
proceed and bad rows are held in quarantined_rows, but this slice must not assume that, it must
match the wiring to the actual current behavior. If the current row-failure path is incompatible
with writing to quarantined_rows (e.g. the whole chunk fails on any row error today), surface it,
do not silently change the processing model; row-quarantine may then need its own slice. Chunk-level
quarantine (the storm fix) does not depend on this and proceeds regardless.

## Depends on

- The existing quarantine tables (quarantine.quarantined_chunks, quarantine.quarantined_rows) and
  their live shape (grounded, not assumed). The decision/migration that created them (find it; it
  explains the chunk-vs-row split and the lifecycle).
- The failure-audit shape (D78) and the FailureCode vocabulary (D79) from the audit arc, the
  correlation the quarantine record and the QUARANTINED audit row both carry.
- The audit Stage enum's reserved QUARANTINED value (no emitter yet, D78-era).
- The consumer catch path (orchestrate.py, the 30b _FlowContext and catch-all) where the
  allowlist decision and the ack happen.
- dis-rls (the write path posture, tenant-scoped), dis-core (UUIDv7, errors).
- Decisions in force: D78/D79 (the seam), D43/D44 (audit posture), hard rule 11 (audit
  fire-and-forget, preserved). The Pub/Sub dead-letter policy (the infra backstop, separate).
- Decision to REGISTER (operator assigns at the gate): the storm-stopper posture, a
  known-deterministic failure (narrow allowlist) is written to quarantine and ACKED (not nacked),
  breaking the redeliver loop; the QUARANTINED audit emitter is implemented carrying the D78 shape;
  the dis-quarantine lib owns the write path. The retryable-vs-deterministic split is a narrow
  allowlist this slice, widenable later.

## Goal

After this slice: a deterministic failure on the allowlist is written to the quarantine store
(chunk-level -> quarantined_chunks with status=NEW; row-level -> quarantined_rows) and the message
is ACKED, so it stops redelivering, the storm class is broken at the source. A QUARANTINED audit
row is emitted carrying the failure-audit correlation, so the trail shows the disposition and
Amit's screen reads real held data. Non-allowlisted failures keep today's behavior (nack), with the
Pub/Sub dead-letter policy as the backstop. No replay, no drainer, no broad classification yet.

## Task

Decompose in plan mode and show the design before code. Touches a new lib (dis-quarantine), the
streaming-consumer, and the dis-audit Stage emit path (the QUARANTINED emitter). Confirm live
shapes; do not assert them.

Plan-mode grounding (ERROR, not skip):
- The live shape of quarantine.quarantined_chunks and quarantine.quarantined_rows (columns, the
  status default NEW, the FK to identity_mirror.tenants, RLS policy, indexes); the decision that
  created them.
- How the consumer catch path is structured post-30b/30c (the _FlowContext, the catch-all, the
  ack/nack mechanism, where the failure_code is classified): where the allowlist decision and the
  ack go.
- The row-failure path TODAY (the required grounding above): what happens to a chunk with bad
  rows now.
- The QUARANTINED Stage value and how the existing audit emit path would emit it (the same emit
  the failure-audit rows use).
- The mapping from a failure (the exception + the _FlowContext ids) to a quarantine record's
  columns, per table: which columns are always known, which are known-where (mapping_version_id
  post-lookup; data_ingress_event_id post-bronze), what goes in failure_context JSONB.
- The FailureCode allowlist: confirm each proposed member is genuinely deterministic (retrying
  cannot help), so acking is correct.

Design deliverables shown in the plan:
1. The dis-quarantine lib: hold_chunk(...) and the row form, the record models, the write path
   (rls_session, tenant-scoped), and how a failure maps to the columns of each table.
2. The consumer wiring: the allowlist, the chunk-vs-row routing, and the ack-instead-of-nack on a
   quarantined failure (the storm fix). Show explicitly that a quarantined failure ACKS (leaves the
   queue) and a non-allowlisted failure still NACKS (today's behavior).
3. The QUARANTINED audit emit carrying the D78 shape.
4. The row-failure-path finding and how row-quarantine wiring matches the current behavior (or the
   flag that it needs its own slice).

Tests (same commit):
- A deterministic chunk-level failure (the MAPPING_CONFIG_INVALID / empty-mapping case) writes a
  quarantined_chunks row (status=NEW, the correlation columns populated) AND the message is ACKED
  (does not redeliver) AND a QUARANTINED audit row is emitted. This is the storm-fix proof: the
  loop is broken.
- A row-level validation failure writes quarantined_rows row(s) with the row correlation
  (row_offset, the failure detail) and a QUARANTINED audit row; the chunk's good rows behave per
  the grounded current model.
- A non-allowlisted failure (e.g. INFRA_FAILURE / a transient) still NACKS (today's behavior
  unchanged), proving the allowlist is narrow and the change is scoped.
- The quarantine write is tenant-scoped (RLS): a row written under tenant A is not visible under
  tenant B.
- The QUARANTINED audit row carries the failure-audit shape (trace_id, tenant_id,
  data_ingress_event_id, mapping_version_id where known, stable failure_code, stage).
- The audit fire-and-forget posture is unchanged; the quarantine write failing does not wedge the
  consumer (define its posture: a quarantine-write failure should not silently drop the message
  into the ack-and-lose hole, surface the chosen behavior).

## What this slice does NOT do

No DLQ drainer service. No replay / lifecycle transitions (only status=NEW is written). No broad
failure classification (narrow allowlist only). No worker quarantine wiring. No table creation or
schema change (the tables exist; flag a genuine gap, do not add speculatively). No services/dis-ui
edit. No change to the audit fire-and-forget posture. No change to the Pub/Sub dead-letter policy
(infra, separate).

## Open questions for plan mode

1. The quarantine-write failure posture: if writing to the quarantine store fails (store down),
   what happens to the message? It must NOT be acked-and-lost (that drops data) and must NOT
   nack-loop forever. Lean: nack so it redelivers (the Pub/Sub dead-letter policy then backstops it
   after the cap). CC confirms against the consumer's ack/nack structure.
2. The row-failure path (the required grounding): does row-quarantine wiring match the current
   behavior, or does it need its own slice?
3. The exact allowlist membership (which FailureCodes are truly deterministic).

## Acceptance criteria

- A deterministic chunk-level failure on the allowlist writes a quarantined_chunks row (status=NEW,
  correlation populated), ACKS the message (no redelivery, the storm broken), and emits a
  QUARANTINED audit row.
- A row-level validation failure writes quarantined_rows with row correlation and emits QUARANTINED;
  the chunk's good-row behavior matches the grounded current model.
- A non-allowlisted failure still nacks (today's behavior unchanged).
- The quarantine write is tenant-scoped (RLS proven); the QUARANTINED audit row carries the D78
  shape.
- The quarantine-write-failure posture is defined and tested (no ack-and-lose, no infinite loop).
- The audit fire-and-forget posture is unchanged.
- The dis-quarantine lib owns the write path (reusable; the worker can adopt it later).
- No drainer, no replay, no broad classification, no worker wiring, no schema change, no dis-ui
  edit.
- The register entry (the storm-stopper posture; the QUARANTINED emitter; the dis-quarantine lib;
  the narrow allowlist) is recorded.
- make check / lint / mypy clean; tests in the same commit.
