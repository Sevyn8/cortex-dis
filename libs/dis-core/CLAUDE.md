# libs/dis-core — Claude Code Context

Loaded when Claude Code works in `libs/dis-core/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

Foundational utilities: `errors` (the `DisError` root), `identifiers`, `ids` (UUIDv7), `trace_id`, `timestamps`, structured `logging`, the `bq.BqClient` stub, and the `identity` client. Dependency-light; no business logic.

For interfaces, types, file structure, see `README.md` in this directory.

## Rules specific to this lib

- BqClient is the ONLY way to query BigQuery in DIS. Direct `google-cloud-bigquery` import is forbidden by CI lint. It auto-injects `WHERE tenant_id = :tenant_id`; callers pass tenant_id. Phase 1 it is an inert stub (no BigQuery until Phase 3 / Slice 21; `decisions.md` D34).
- IDs: use `ids.new_uuid7`; do NOT use `uuid4` anywhere in DIS code.
- All errors subclass `errors.DisError`. `errors.py` is leaf-level (imports nothing first-party); `identity` and the other modules import *from* it. `dis-core` never imports `dis-testing`.
- **Identity is the internal UUID (D37 RESOLVED, Slice 9a).** `identifiers.py` defines `TenantId`/`StoreId` as **`UUID`**; `identity/models.py` carries `tenant_id`/`store_id` as `UUID` too — the historical name collision is dissolved and the invented external `t_*`/`s_*` aliases are retired (D52). The `Identity` contract model also carries the authoritative Customer Master codes (`display_code`/`store_code`, readability only, nullable — D55); never write identity from a code. `us_*`/`ec_*` patterns remain (genuine CM artifact forms).

## References

- `README.md` (this directory) — interface and structure.
- Root `CLAUDE.md` — project-wide invariants.
