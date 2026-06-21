# services/nightly-batch — Claude Code Context

Loaded when Claude Code works in `services/nightly-batch/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

Scheduled job during retail off-hours. Two phases: (1) daily compute job invokes `daily-compute` for signal updates; (2) Cloud SQL → BigQuery copy + retention eviction (drop partitions older than retention window, default 35 days).

For the EPE block (purpose, entry, process, exit), file structure, and operational detail, see `README.md` in this directory. For the current build slice, see the slice doc in `docs/slices/`.

**Status:** v1.0.

## Rules specific to this service

- Writes to: BigQuery `canonical_history.*` tables (via WRITE_TRUNCATE per partition); drops old Postgres partitions. Do not write to other tables or topics from here.
- Reads from: `canonical.store_sku_sale_events`, `canonical.store_sku_change_events`, `canonical.store_sku_signal_history`.
- All Postgres access uses `libs/dis-rls`. No raw SQLAlchemy sessions.
- All BigQuery access uses `libs/dis-core` BqClient.
- All audit emission uses `libs/dis-core` audit helpers.
- WRITE_TRUNCATE per partition for idempotency.
- Partition drop only AFTER successful BQ load + retention window elapsed.
- Retention default 35 days; configurable.
- Compute phase runs first; export runs only after compute succeeds.

## References

- `README.md` (this directory) — EPE block, file structure, behavioural detail.
- Root `CLAUDE.md` — project-wide invariants.
- `docs/architecture.md` §4 — module rationale.
- `docs/decisions.md` — indexed decision register.

## When uncertain

Refer to the current slice doc in `docs/slices/`. If silent there, refer to architecture. If still uncertain, ask before coding.
