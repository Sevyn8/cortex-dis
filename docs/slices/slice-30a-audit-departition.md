# Slice: de-partition audit.events (remove the silent write-cliff)

A small, standalone DDL slice. `audit.events` is range-partitioned by `event_date` with a fixed
set of daily partitions created once at bootstrap and no DEFAULT partition and no partition
automation (D45). Once the calendar passes the last partition, every audit write hits "no
partition found", which the audit writer's fire-and-forget swallows, so audit silently stops
recording. This slice removes that failure mode for beta by converting `audit.events` to a plain
(non-partitioned) table. Partitioning, with proper automation, is re-introduced later by the BQ
archive + eviction slice (Slice 21), which is the slice that actually needs it.

This is a precursor to the audit-coverage slice: coverage work (making stages emit, populating
columns) is pointless while the sink can silently drop writes. De-partition first, then build
coverage on a sink that reliably accepts writes.

## Why de-partition rather than add automation

The three reasons audit.events was partitioned, eviction (cheap partition drop), query
performance at volume, and the nightly BQ archive export, are all Phase-3 / Slice-21 concerns
(D29, D34). None is needed at beta scale (~150K events/day sits comfortably in a plain table).
Keeping daily partitions today delivers none of those benefits while imposing ongoing upkeep
(someone or something must keep creating future partitions) and the silent-cliff if that upkeep
lapses, which it has. De-partitioning removes the upkeep and the cliff. The worst case for a
plain table at beta scale is benign: it grows long and queries eventually slow, a visible,
gradual problem, not silent blindness. When Slice 21 builds the real retention policy (BQ
archive + partition eviction), it re-introduces partitioning WITH automation, as a coherent
piece, owning the (small, at beta scale) cost of partitioning a table that has accumulated rows.

## Scope boundary (important)

This slice touches `audit.events` ONLY. The canonical event tables (`store_sku_sale_events`,
`store_sku_change_events`), `signal_history`, and the staging event tables share the same
bootstrap-only daily-partition scheme, but they are NOT in scope and must NOT be changed: their
partitioning is the load-bearing substrate for the D29 / D34 35-day-rolling-buffer + eviction
design, and crucially they fail LOUD on a missing partition (a batch nack), not silently, so
they carry no silent-loss risk. audit.events is the silent-loss outlier (fire-and-forget
swallows the missing-partition error) and the only table this slice converts. The canonical
tables' partition automation is Slice 21's concern.

## Depends on

- The current `audit.events` DDL and the bootstrap migration that created its partitions
  (`alembic/versions/0001_bootstrap.py`, the 7 daily partitions). This slice adds a NEW migration
  (do not edit 0001).
- The audit writer (`libs/dis-audit`, PostgresAuditWriter) writes via `rls_session`; the table's
  RLS posture and the writer must keep working unchanged against the de-partitioned table.
- The data is disposable: all current rows (~1.9M, storm junk on the local dev DB, nothing
  deployed) are dropped; no preservation required (operator confirmed). The storm has been
  arrested (consumer stopped, poison message drained).
- Decisions in force: D45 (the partition-coverage gap this resolves), D34 (Cloud SQL is the
  Phase-1 audit sink; BQ Phase 3), D29 (the 35-day buffer + BQ archive that re-introduces
  partitioning at Slice 21), D43/D44 (audit writer posture, unchanged), hard rule 11
  (fire-and-forget, unchanged, but its biggest silent-loss source is removed by this slice).
- Decision to REGISTER (operator assigns the number at the commit gate): audit.events is
  de-partitioned to a plain table for beta to remove the D45 silent write-cliff; partitioning
  with automation is re-introduced at Slice 21 (BQ archive + eviction). D45 moves toward
  RESOLVED-for-beta (the silent-loss path is closed; the eventual retention policy remains
  Slice 21).

## Goal

After this slice, `audit.events` is a plain non-partitioned table that accepts a write for any
`event_date` without a missing-partition error. The audit writer works unchanged. All prior
(storm-junk) rows are gone. The silent write-cliff (D45) is closed for beta. No other partitioned
table is touched.

## Task

A single Alembic migration converting `audit.events` from partitioned to plain, plus
reconciliation so the fresh-bootstrap path and the migrate-existing path land in the same place.
Confirm live shapes in plan mode; do not assert them.

Plan-mode grounding (ERROR, not skip):
- The live `audit.events` definition: that it is PARTITION BY RANGE (event_date), the live
  partition list (events_p20260601 ... events_p20260607), the PK ((id, event_date) — partitioned
  tables require the partition key in the PK), all constraints (the outcome CHECK, the event_scope
  CHECK, the event_date-matches CHECK, the FK to identity_mirror.tenants), the indexes, and the
  RLS policy. State which of these are artifacts of partitioning (e.g. the event_date-in-PK
  requirement, the event_date-matches CHECK) versus intrinsic to the table.
- The bootstrap migration's partition creation for audit.events (0001) and how a NEW migration
  converts the existing table, AND how a fresh bootstrap should now produce the table already
  un-partitioned (so a fresh DB and a migrated DB end identical). Reconcile: either the new
  migration handles conversion and 0001 still creates the partitioned form (then the new
  migration converts it), or the manifest/schema file is updated so fresh bootstrap is plain.
  State the chosen reconciliation and prove fresh-bootstrap == migrated end state.
- The PK decision once un-partitioned: PK can become just (id) (cleaner, id is uuidv7 unique) or
  stay (id, event_date). Recommend, with reasoning. Drop the event_date-matches CHECK if it only
  existed to satisfy partition routing (confirm).
- Confirm the audit writer (PostgresAuditWriter) needs NO code change against the plain table
  (it INSERTs columns; partitioning was transparent to it). If any writer assumption depends on
  partitioning, surface it.

Implementation:
- The new Alembic migration: drop the partitioned `audit.events` (and its partitions) and create
  a plain `audit.events` with the same columns, the same non-partition constraints (outcome
  CHECK, event_scope CHECK, FK to tenants), the same RLS policy, and the chosen PK. event_date
  stays a column (still useful for querying/retention later) but is no longer a partition key.
  Since the data is disposable, a drop-and-recreate is acceptable; do NOT write data-migration
  code to preserve the storm rows. downgrade() recreates the partitioned form (symmetric, best
  effort) or documents that downgrade re-partitions with a fresh window.
- Reconcile the fresh-bootstrap path so a brand-new DB bootstraps audit.events plain (update the
  schema manifest / DDL file as the grounding step determines), keeping fresh == migrated.
- Keep RLS identical: the de-partitioned table must enforce the same tenant isolation as before.

## What this slice does NOT do

No change to any other partitioned table (canonical event tables, signal_history, staging, all
keep their partitioning, D29/Slice 21 substrate). No partition automation (that is Slice 21). No
BQ work. No audit writer code change (unless grounding surfaces a genuine dependency). No
coverage/emission changes (that is the audit-coverage slice). No edit to services/dis-ui. No data
preservation (storm rows are dropped). No retention/eviction policy (Slice 21).

## Open questions for plan mode

1. The PK once un-partitioned: (id) alone vs (id, event_date), CC recommends.
2. The fresh-bootstrap reconciliation shape (new migration converts vs schema file updated to
   plain), so fresh == migrated, CC states the chosen approach and proves equivalence.
3. downgrade() behavior: re-partition symmetrically, or document a fresh-window re-partition.

## Acceptance criteria

- `audit.events` is a plain, non-partitioned table; a write for ANY event_date succeeds with no
  missing-partition error (test: insert a row dated well outside the old 2026-06-01..07 window).
- The audit writer (PostgresAuditWriter) writes successfully against the plain table with no code
  change; RLS tenant isolation is identical to before (test-proven).
- All prior rows are gone (drop-and-recreate; no preservation).
- The same non-partition constraints (outcome CHECK, event_scope CHECK, FK to tenants) and the
  RLS policy are present on the plain table; the chosen PK is in place.
- A fresh bootstrap and a migrated existing DB produce an identical `audit.events` shape
  (reconciled; proven).
- No other partitioned table is altered (verified by diff / introspection).
- The register entry (de-partition for beta; partitioning + automation re-introduced at Slice 21;
  D45 -> RESOLVED-for-beta) is recorded at the gate.
- `make check` / lint / mypy clean; the migration up/down cycle is tested.
