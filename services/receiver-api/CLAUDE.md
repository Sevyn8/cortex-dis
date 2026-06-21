# services/receiver-api — Claude Code Context

Loaded when Claude Code works in `services/receiver-api/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

API/webhook ingress receiver. Accepts pushed payloads from tenant systems (POS webhooks, third-party event streams). One of four DIS receivers.

For the EPE block (purpose, entry, process, exit), file structure, and operational detail, see `README.md` in this directory. For the current build slice, see the slice doc in `docs/slices/`.

**Status:** deferred (not v1.0).

## Rules specific to this service

- Writes to: `bronze.data_ingress_events`, GCS, Pub/Sub `ingress.ready`. Do not write to other tables or topics from here.
- Reads from: `identity_mirror` (via identity-service), `config.source_mappings`.
- All Postgres access uses `libs/dis-rls`. No raw SQLAlchemy sessions.
- All BigQuery access uses `libs/dis-core` BqClient.
- All audit emission uses `libs/dis-core` audit helpers.
- This service generates the request's `trace_id` at request entry.
- PII tokenization happens here before any persistence. Use `libs/dis-pii`.
- All GCS access uses `libs/dis-storage`.

## References

- `README.md` (this directory) — EPE block, file structure, behavioural detail.
- Root `CLAUDE.md` — project-wide invariants.
- `docs/architecture.md` §4 — module rationale.
- `docs/decisions.md` — indexed decision register.

## When uncertain

Refer to the current slice doc in `docs/slices/`. If silent there, refer to architecture. If still uncertain, ask before coding.
