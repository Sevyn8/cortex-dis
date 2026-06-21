# services/identity-service — Claude Code Context

Loaded when Claude Code works in `services/identity-service/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

Tenant/store identity service. Resolves and validates tenant/store identifiers; mediates Customer Master access.

For the EPE block (purpose, entry, process, exit), file structure, and operational detail, see `README.md` in this directory. For the current build slice, see the slice doc in `docs/slices/`.

**Status:** v1.0.

## Rules specific to this service

- Writes to: Local cache (in-process or Redis); on admin DB writes, publishes `identity.changed` Pub/Sub. Do not write to other tables or topics from here.
- Reads from: Customer Master (admin DB), local cache.
- All Postgres access uses `libs/dis-rls`. No raw SQLAlchemy sessions.
- All BigQuery access uses `libs/dis-core` BqClient.
- All audit emission uses `libs/dis-core` audit helpers.
- Stale-while-error: cache returns stale entries (up to 5 min) on admin DB error.
- Four interface methods: `resolve_from_token`, `resolve_from_upload`, `resolve_from_endpoint`, `validate`. See architecture §4.2.
- Does NOT verify user JWTs (Customer Master does that). This service handles data-plane identity only.

## References

- `README.md` (this directory) — EPE block, file structure, behavioural detail.
- Root `CLAUDE.md` — project-wide invariants.
- `docs/architecture.md` §4 — module rationale.
- `docs/decisions.md` — indexed decision register.

## When uncertain

Refer to the current slice doc in `docs/slices/`. If silent there, refer to architecture. If still uncertain, ask before coding.
