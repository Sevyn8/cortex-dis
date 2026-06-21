# services/daily-compute — Claude Code Context

Loaded when Claude Code works in `services/daily-compute/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

Postgres-local incremental daily compute of derived attributes (`velocity_7day`, `stock_age_days`, `unit_cost_trend_30day`). Writes new `signal_history` rows; updates `store_sku_current_position`.

For the EPE block (purpose, entry, process, exit), file structure, and operational detail, see `README.md` in this directory. For the current build slice, see the slice doc in `docs/slices/`.

**Status:** v1.0.

## Rules specific to this service

- Writes to: `canonical.store_sku_signal_history` (insert), `canonical.store_sku_current_position` (update derived columns only). Do not write to other tables or topics from here.
- Reads from: Yesterday's `signal_history` rows, today's events, mapping/source metadata.
- All Postgres access uses `libs/dis-rls`. No raw SQLAlchemy sessions.
- All BigQuery access uses `libs/dis-core` BqClient.
- All audit emission uses `libs/dis-core` audit helpers.
- Incremental: yesterday's row + today's events → today's row. NOT full-window recomputation.
- Slow-path fallback reads from BigQuery `canonical_history.*` when Cloud SQL retention has aged out the needed window.
- Updates only the derived columns on `store_sku_current_position`; does not touch raw event-sourced state.

## References

- `README.md` (this directory) — EPE block, file structure, behavioural detail.
- Root `CLAUDE.md` — project-wide invariants.
- `docs/architecture.md` §4 — module rationale.
- `docs/decisions.md` — indexed decision register.

## When uncertain

Refer to the current slice doc in `docs/slices/`. If silent there, refer to architecture. If still uncertain, ask before coding.
