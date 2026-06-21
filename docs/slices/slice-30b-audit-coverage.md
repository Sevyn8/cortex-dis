# Slice 30b: audit coverage and the failure-audit shape

Make the audit trail complete and legible: every pipeline stage emits, every failure row carries
the columns it needs to be debugged and correlated, and the duplicate/failure detail that lives
in JSONB today is promoted to first-class, queryable columns. The load-bearing piece is the
failure-audit shape, the set of columns a FAILURE audit row must carry so an operator (and the
quarantine console) can answer "what failed, where, why, and how do I correlate it to the held
row." This shape is the seam the quarantine work consumes.

Builds on Slice 30a (audit.events is now a plain table that reliably accepts writes; the silent
write-cliff is closed). Coverage work was moot before that; now the sink is sound, this slice
makes the content complete.

Two tiers, both in scope this slice.

## Tier 1: the failure-audit spine (code-only)

The coverage map showed the schema is well-equipped (failure_code, failure_message,
data_ingress_event_id, mapping_version_id, the row metrics all exist as columns) but emitters
under-populate them, the principal example being the consumer catch-all, which on every
deterministic failure writes only trace_id/tenant_id/failure_code/failure_message and leaves the
known bronze_id and mapping_version_id NULL (the de-partition precursor's storm produced ~792K
such rows, the live proof). Tier 1 is emission discipline: populate the columns that exist, fill
the silent stages, make failures legible.

- **The consumer catch-all populates its known correlation columns.** Where a failure occurs
  after the bronze fetch, the audit row carries `data_ingress_event_id`; after the mapping
  lookup, it carries `mapping_version_id`. The catch-all must thread these through rather than
  burying the ids in `failure_message`.
- **Stable, enumerated `failure_code` vocabulary.** Today `failure_code` is the exception class
  name, an unstable vocabulary that defeats "all X failures" queries. Define a stable enumerated
  set of failure codes spanning the failure paths (preflight, PII, mapping-config, validation,
  the D63 incomplete-mapping miss, store/template/structural rejections) and emit those.
- **Audit the dis-ui 4xx family.** Multipart-parse, tier-0, template (404/409), and store
  (404/409) rejections are silent today, the trace_id is minted but the audit story starts only
  at the GCS write. Emit a FAILURE audit row for each with its rejection reason.
- **Emit RETRIED on redelivery.** Redelivery is visible today only as repeated rows per
  trace_id; emit the RETRIED outcome so retries are legible (this is also why the storm was
  indistinguishable mass rather than a clean retry count).
- **Small population gaps.** The worker PII-block path passes `bronze_id` (the bronze row exists
  and the id is known); the worker path-mismatch fills its `event_data`.

## Tier 2: enrichment and the schema promotion (DDL)

Tier 2 promotes detail from JSONB to first-class columns for queryability, and adds timing and
guard hardening. The outcome-vocab and prior_trace_id pieces REVISE D42's deliberate
event_data-JSONB resolution (D42 chose JSONB for v1.0 and was marked RESOLVED via that path, see
the Slice-10 register note); this slice consciously supersedes that choice to make the
duplicate/failure detail queryable for the audit and quarantine consoles. Frame it as a
revision-with-reason, not a gap-fill.

- **Extend the outcome CHECK for the duplicate distinction.** Promote `DUPLICATE_NOOP` and
  `DUPLICATE_OVERWRITTEN` from `event_data` to first-class `outcome` values (the CHECK currently
  permits only SUCCESS/FAILURE/SKIPPED/RETRIED). The consumer's duplicate audit (D33) then sets
  the `outcome` column rather than stuffing the kind into `event_data`. Migration on the
  outcome CHECK + the `dis-audit` Outcome enum + the consumer emit site.
- **Promote `prior_trace_id` to a column.** It lives in `event_data` today (duplicate hits, the
  worker SKIPPED dedup). Add the column and populate it where duplicates/dedup are detected, so
  "what redelivered from what" is queryable.
- **Populate `duration_ms` at every stage.** No site populates it today, so per-stage latency
  (and the architecture's latency SLO) is unmeasurable. Add a timing seam in the emit path so
  each stage records its elapsed duration.
- **Harden the drift guard.** The `dis-audit` model-vs-schema guard checks the column-name set
  only (both directions), not type or nullability, so a type narrowing passes the guard and
  fails at INSERT, which fire-and-forget then swallows (the silent-loss class the storm and D45
  share). Extend the guard to check type and nullability, so schema drift fails loud at the
  guard, not silently at runtime.

## Depends on

- Slice 30a (audit.events is plain; the Tier 2 migration is on the plain table, same
  fresh-vs-migrate reconciliation pattern, edit events.sql + new migration + prove fresh ==
  migrated). D77 (de-partition), D45 (RESOLVED-for-beta).
- The `dis-audit` lib (the writer, the AuditEvent model, the Stage/Outcome/EventScope enums, the
  drift guard) and its emit sites across dis-ui-server, csv-ingest-worker, streaming-consumer.
- The coverage map (the live emit-site inventory + the gap list this slice closes).
- Decisions in force: D34 (Cloud SQL Phase-1 sink; BQ Phase 3), D33 (dedup keys + the duplicate
  audit), D43 (writer requires non-NULL tenant_id), D44 (audit tolerates duplicates), D63 (the
  incomplete-mapping miss is a defined disposition that must audit), hard rule 11 (fire-and-
  forget, preserved). D42 is the decision this slice REVISES (the JSONB-vs-columns choice).
- Decision to REGISTER (operator assigns numbers at the commit gate): (1) D42 is revised, the
  duplicate detail (DUPLICATE_* outcome, prior_trace_id) is promoted from event_data to
  first-class columns for queryability (supersedes the Slice-10 JSONB resolution; cite the
  reason: the audit and quarantine consoles need to query by outcome/prior_trace_id). (2) the
  failure-audit shape, the columns a FAILURE row must carry (data_ingress_event_id,
  mapping_version_id where known, stable failure_code), registered as the contract the quarantine
  work consumes. (3) the stable failure_code vocabulary as an enumerated set.

## Goal

After this slice: every pipeline stage emits an audit row (no silent stages on the happy or
failure path except the documented structural cases); every FAILURE row carries the correlation
columns it can know (data_ingress_event_id, mapping_version_id) and a stable failure_code;
duplicates set the DUPLICATE_* outcome and prior_trace_id as columns; RETRIED is emitted on
redelivery; duration_ms is populated per stage; the drift guard fails loud on type/nullability
drift. The failure-audit shape is defined and is the documented seam for the quarantine work.

## Task

Decompose in plan mode and show the design before code. Touches dis-ui-server, csv-ingest-worker,
streaming-consumer, dis-audit (lib), and a migration. Confirm live shapes (the emit sites, the
current failure_code values, the timing seams, the de-partitioned table); do not assert them.

Plan-mode grounding (ERROR, not skip):
- The full emit-site inventory per service (from the coverage map, re-confirmed live): which
  stages emit, which are silent, which columns each populates vs leaves NULL.
- The consumer catch-all (orchestrate.py): where bronze_id and mapping_version_id are in scope at
  the point of failure (post-fetch / post-lookup) so the catch-all can populate them; confirm the
  exception-to-stage mechanism (exc._dis_stage) and how a stable failure_code maps onto it.
- The current failure_code values in live use (the exception-class-name set) so the stable enum
  is a superset that loses no information.
- The duplicate audit emit site (D33, sinks/audit.py) and how moving DUPLICATE_*/prior_trace_id
  from event_data to columns changes it; the worker SKIPPED dedup site too.
- The de-partitioned audit.events (post-30a): the migration adds prior_trace_id (uuid, nullable)
  and extends the outcome CHECK; reconcile events.sql + a new migration + prove fresh == migrated
  (the 30a pattern). The drift guard then covers the new columns.
- The timing seam for duration_ms: where a stage's start/end is available to compute elapsed,
  per service, without restructuring the emit path.
- The drift guard (dis-audit test): how to extend it from name-set to type/nullability against
  the live schema.

Design deliverables shown in plan for review:
1. The stable failure_code enum (the full set, mapped to every failure path) and the extended
   Outcome enum (+ DUPLICATE_NOOP/DUPLICATE_OVERWRITTEN), coherent and non-overlapping.
2. The failure-audit shape: the columns a FAILURE row must carry, per failure path, with which
   are always-known vs known-where (post-fetch/post-lookup). This is the quarantine seam, name it
   explicitly.
3. The migration (outcome CHECK extension + prior_trace_id column) with the fresh-vs-migrate
   reconciliation, and the drift-guard hardening that covers the new columns.
4. The duration_ms timing approach per service.
5. The per-stage/per-failure emission changes across the three services.

Tests (same commit):
- The catch-all failure populates data_ingress_event_id (post-fetch) and mapping_version_id
  (post-lookup) with a stable failure_code, proven for the key failure paths (mapping-config,
  validation, the D63 miss).
- The dis-ui 4xx family emits a FAILURE audit row with the rejection reason (multipart, tier-0,
  template, store).
- RETRIED is emitted on redelivery.
- Duplicates set the DUPLICATE_* outcome column and prior_trace_id column (not event_data),
  proven for NOOP and OVERWRITTEN.
- duration_ms is populated and non-negative at the stages it covers.
- The migration up/down cycle; fresh == migrated; the drift guard now fails on a type/nullability
  mismatch (prove it catches a narrowing, not just a name change).
- Existing audit tests stay green; the writer still fire-and-forgets (never raises).

## What this slice does NOT do

No quarantine, no QUARANTINED-stage emitter (the quarantine work owns that; this slice defines
the failure-audit shape it consumes). No BQ work (Phase 3 / Slice 21). No re-partitioning
(Slice 21). No change to the dedup keys (D33/D58/D65). No new correlation columns beyond
prior_trace_id (store_id/source_id/source_event_id/row_hash stay in event_data unless a failure
path genuinely needs one promoted, flag if so, do not add speculatively). No edit to
services/dis-ui. No change to the fire-and-forget posture (hard rule 11, preserved).

## Open questions for plan mode

1. The stable failure_code enum membership, the exact set covering every failure path without
   overlap (CC proposes; operator approves).
2. duration_ms: how each stage's elapsed is sourced without restructuring emit paths (CC states
   per service).
3. Whether any correlation field beyond prior_trace_id genuinely needs promoting from event_data
   for a failure path (CC flags; default is no).
4. The unparseable-envelope silent case (D43, tenant unknowable): document as silent-by-design or
   emit a tenant-less row, CC recommends; default is document-and-leave.

## Acceptance criteria

- The consumer catch-all populates data_ingress_event_id and mapping_version_id where known, with
  a stable failure_code, on the key failure paths (test-proven; the storm's NULL-id failures no
  longer possible).
- The dis-ui 4xx family emits FAILURE audit rows with rejection reasons.
- RETRIED is emitted on redelivery; duration_ms is populated per stage.
- Duplicates set DUPLICATE_NOOP/DUPLICATE_OVERWRITTEN as the outcome column and prior_trace_id as
  a column (D42 revision); the consumer duplicate emit and the worker SKIPPED dedup use the
  columns.
- The migration (outcome CHECK extension + prior_trace_id) lands on the plain audit.events; fresh
  == migrated (proven); the drift guard now checks type/nullability and fails loud on drift
  (proven by a narrowing test).
- The failure-audit shape is documented as the quarantine seam.
- The fire-and-forget posture is unchanged (the writer never raises); existing audit tests green.
- No quarantine work, no BQ, no re-partitioning, no dedup-key change, no dis-ui edit.
- The register entries (D42 revision; the failure-audit shape; the failure_code enum) are
  recorded.
- make check / lint / mypy clean; tests in the same commit.
