# Slice 30c: audit Tier 2 — outcome vocabulary, prior_trace_id, drift-guard hardening (the D42 revision)

The schema-and-promotion half of audit coverage, deferred from 30b as its own session because it
carries a migration and a deliberate decision revision. 30b made failures legible in code (the
FailureCode vocabulary, the catch-all id population, the failure-audit shape); 30c promotes the
duplicate detail from event_data JSONB to first-class, queryable columns, hardens the drift guard,
and adds per-stage timing's schema home where needed.

The load-bearing framing: this slice REVISES D42, it does not fix it. D42 was marked RESOLVED by
Slice 10 via a deliberate choice to keep the duplicate detail (DUPLICATE_*, prior_trace_id,
row_hash, dedup_key) in event_data JSONB ("rejected as over-engineering for v1.0"). 30c
consciously supersedes that choice because the audit and quarantine consoles need to query by
outcome and prior_trace_id. The migration docstring and the register entry cite D42's prior
resolution as the thing being revised, with the reason (queryability for the consoles). Do not
present D42 as broken.

Builds on 30b (the failure-audit shape, D78; the FailureCode vocabulary, D79) and 30a (the plain
audit.events, D77, on which this migration is additive).

## Scope

In:
- Migration 0008: add `prior_trace_id` (uuid, nullable) column; extend the outcome CHECK from 4
  values to 6 (add DUPLICATE_NOOP, DUPLICATE_OVERWRITTEN). Additive and gated; the
  fresh-vs-migrate reconciliation (edit events.sql + the migration + prove fresh == migrated, the
  30a pattern); a refuse-loudly downgrade (a downgrade that would orphan DUPLICATE_* rows under
  the restored 4-value CHECK must fail loud, not silently corrupt).
- The `dis-audit` Outcome enum gains DUPLICATE_NOOP and DUPLICATE_OVERWRITTEN; the AuditEvent
  model and the writer INSERT gain prior_trace_id.
- The consumer duplicate emit (sinks/audit.py emit_duplicate, D33) switches from
  event_data{duplicate, prior_trace_id} to outcome=DUPLICATE_* + prior_trace_id column; row_hash
  and dedup_key STAY in event_data (only the queryable-by fields are promoted).
- The worker dedup-SKIPPED and resume emits switch prior_trace_id from event_data to the column;
  resume becomes RETRIED (the resume path is a retry-completion, made legible).
- Drift-guard hardening: extend the dis-audit model-vs-schema guard from a column-NAME-set match
  to also check type and nullability, so a type narrowing fails loud at the guard rather than
  silently at INSERT (the silent-loss class D45 and the storm shared). With a narrowing proof
  (a synthetic type/nullability mismatch is reported by a pure diff).

Out (owned elsewhere or already done):
- The FailureCode vocabulary, the catch-all id population, the 4xx audit, RETRIED-on-redelivery,
  duration_ms lap timers (all 30b, done). 30c does not re-touch them except where the duplicate
  emit sites change.
- Quarantine, the QUARANTINED emitter, DLQ, delivery_attempt-based retry detection (the
  quarantine work; 30c defines nothing new for it beyond promoting prior_trace_id, which the
  failure-audit seam already named).
- BQ, re-partitioning (Slice 21). No dedup-key change (D33/D58/D65). No services/dis-ui edit. The
  fire-and-forget posture preserved (hard rule 11).

## Depends on

- 30a (audit.events is plain; 0008 is additive on it, same fresh-vs-migrate pattern, down_revision
  chains past 0007). D77.
- 30b (the failure-audit shape D78 names prior_trace_id + DUPLICATE_* as joining the seam here;
  the FailureCode vocabulary D79). The duplicate emit sites 30b left on JSONB are the sites 30c
  promotes.
- The dis-audit lib (the Outcome enum, the AuditEvent model, the writer INSERT, the drift-guard
  test), the consumer duplicate emit (sinks/audit.py), the worker dedup/resume emits.
- Decisions in force: D34 (Cloud SQL Phase-1 sink), D33 (the dedup keys + the duplicate audit
  semantics, unchanged), D44 (audit tolerates duplicates), D45 (the silent-loss class the
  drift-guard hardening closes a slice of), D77/D78/D79. D42 is the decision REVISED.
- Decision to REGISTER (operator assigns the number at the commit gate): D42 is revised — the
  duplicate detail (DUPLICATE_NOOP/DUPLICATE_OVERWRITTEN outcome, prior_trace_id) is promoted from
  event_data JSONB to first-class columns for queryability by the audit and quarantine consoles;
  this supersedes the Slice-10 JSONB resolution (cite it), with row_hash/dedup_key remaining in
  event_data (not queried-by). The drift-guard hardening (type/nullability) is folded under the
  same entry or its own, operator's call.

## Goal

After this slice: a duplicate audit row sets outcome=DUPLICATE_NOOP or DUPLICATE_OVERWRITTEN (a
queryable column, not a JSONB key) and carries prior_trace_id as a column; the worker resume path
is RETRIED; the drift guard fails loud on a type/nullability mismatch (not just a name change);
the migration is additive on the plain audit.events with fresh == migrated and a refuse-loudly
downgrade. "What's the duplicate rate per tenant?" and "what redelivered from what?" become column
queries. D42 is revised-with-reason, not broken.

## Task

Decompose in plan mode and show the design before code. Touches dis-audit (lib), streaming-consumer,
csv-ingest-worker, and a migration. Confirm live shapes; do not assert them.

Plan-mode grounding (ERROR, not skip):
- The live audit.events post-30a/30b: confirm plain, PK (id), the 4-value outcome CHECK, no
  prior_trace_id column, and that the 0008 ADD COLUMN + CHECK swap is additive on real rows (the
  table now accrues live data, not drop-recreate). State the events.sql edit + the migration + the
  fresh==migrated proof, and the refuse-loudly downgrade (a downgrade with DUPLICATE_* rows
  present must fail, like the 0005 precedent).
- The Outcome enum and how it mirrors the live CHECK; the AuditEvent model + the writer INSERT
  and how prior_trace_id threads through (db_column_names should pick it up; confirm).
- The consumer emit_duplicate (sinks/audit.py): the current event_data shape, and that the hit
  kind strings are already exactly DUPLICATE_NOOP/DUPLICATE_OVERWRITTEN (so outcome=Outcome(kind)
  is a clean move); what stays in event_data (row_hash, dedup_key).
- The worker dedup-SKIPPED and resume emits: the current shapes; the resume→RETRIED change.
- The drift guard (the dis-audit test): how to extend from name-set to type/nullability against
  the live information_schema, and the narrowing proof shape (a pure diff over a synthetic
  mismatch, without mutating the live DB).
- The two migration-test pin moves (the head pin in the prior migration test; any 0007/0008
  head assert).

Design deliverables shown in the plan for review:
1. Migration 0008: the ADD COLUMN + the outcome CHECK swap, the events.sql edit, the
   fresh==migrated reconciliation, the refuse-loudly downgrade. State the additive-on-real-rows
   handling (no drop-recreate; the table has live data now).
2. The Outcome extension + the AuditEvent/_INSERT prior_trace_id threading + the drift-guard
   hardening (the type/nullability diff + the narrowing proof).
3. The duplicate-emit promotion: the consumer emit_duplicate and the worker dedup/resume sites,
   what moves to columns vs stays in event_data, and resume→RETRIED.

Tests (same commit):
- The migration up/down cycle; fresh == migrated (introspection equality); the refuse-loudly
  downgrade (a DUPLICATE_* row present makes downgrade fail loud, proven).
- A duplicate sets outcome=DUPLICATE_NOOP / DUPLICATE_OVERWRITTEN as the column and prior_trace_id
  as the column (consumer NOOP + OVERWRITTEN; worker dedup); row_hash/dedup_key still in
  event_data.
- The worker resume emits RETRIED.
- The drift guard fails loud on a type narrowing AND a nullability flip (the synthetic-mismatch
  proof), and passes clean against the real 24-column schema both directions.
- The fire-and-forget posture is unchanged (the writer still never raises); existing audit tests
  green (the D42 JSONB-shape duplicate test now FLIPS to assert the column shape — that is this
  slice's deliberate change, not a regression).

## What this slice does NOT do

No quarantine / QUARANTINED emitter / DLQ. No BQ, no re-partitioning. No dedup-key change. No
new correlation column beyond prior_trace_id (row_hash/dedup_key/store_id/source_id stay in
event_data; promote only if a console query genuinely needs one, flag, do not add speculatively).
No services/dis-ui edit. No change to the FailureCode vocabulary or the catch-all population (30b,
done). The fire-and-forget posture is preserved.

## Open questions for plan mode

1. Whether the drift-guard hardening rides under the D42-revision register entry or gets its own
   (it's a different concern; operator's call at the gate).
2. The refuse-loudly downgrade shape: pre-check for DUPLICATE_* rows and abort, vs document a
   data-loss downgrade (lean: refuse-loudly, the 0005 precedent).
3. Whether any field beyond prior_trace_id needs promoting from event_data for a console query
   (default no; flag if a path genuinely needs it).

## Acceptance criteria

- Migration 0008 adds prior_trace_id and extends the outcome CHECK to 6 values, additive on the
  plain audit.events; fresh == migrated (proven); downgrade refuses loudly when DUPLICATE_* rows
  exist.
- A duplicate audit sets the DUPLICATE_* outcome column and prior_trace_id column (consumer NOOP +
  OVERWRITTEN, worker dedup); row_hash/dedup_key remain in event_data.
- The worker resume path emits RETRIED.
- The drift guard checks type and nullability and fails loud on a narrowing (proven by a synthetic
  mismatch); passes clean against the live schema both directions.
- The fire-and-forget posture is unchanged; the D42 JSONB duplicate test is flipped to the column
  shape (the deliberate revision), all other audit tests green.
- No quarantine, no BQ, no re-partitioning, no dedup-key change, no dis-ui edit, no FailureCode or
  catch-all change.
- The register entry (D42 revised: duplicate detail promoted to columns for queryability,
  superseding the Slice-10 JSONB resolution; the drift-guard hardening) is recorded.
- make check / lint / mypy clean; tests in the same commit.
