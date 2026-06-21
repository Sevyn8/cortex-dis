# services/quarantine-drainer — Claude Code Context

Loaded when Claude Code works in `services/quarantine-drainer/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

Subscribes to the `quarantine` Pub/Sub topic; writes failure events to the `quarantine` schema in Cloud SQL.

For the EPE block (purpose, entry, process, exit), file structure, and operational detail, see `README.md` in this directory. For the current build slice, see the slice doc in `docs/slices/`.

**Status:** v1.0.

## Rules specific to this service

- Writes to: `quarantine.quarantined_rows`, `quarantine.quarantined_chunks`. Do not write to other tables or topics from here.
- Reads from: Pub/Sub `quarantine`.
- All Postgres access uses `libs/dis-rls`. No raw SQLAlchemy sessions.
- All BigQuery access uses `libs/dis-core` BqClient.
- All audit emission uses `libs/dis-core` audit helpers.
- Raw row payload is NOT stored inline; reference GCS via `gcs_uri` + `row_offset`.
- Failure context (validation error, expected vs actual) stored inline as JSONB.
- Idempotent: same trace_id + row_offset + failure_stage is a unique combination.

## References

- `README.md` (this directory) — EPE block, file structure, behavioural detail.
- Root `CLAUDE.md` — project-wide invariants.
- `docs/architecture.md` §4 — module rationale.
- `docs/decisions.md` — indexed decision register.

## When uncertain

Refer to the current slice doc in `docs/slices/`. If silent there, refer to architecture. If still uncertain, ask before coding.
