# libs/dis-canonical — Claude Code Context

Loaded when Claude Code works in `libs/dis-canonical/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

Canonical schema models (Pydantic) and the canonical row builder. The source of truth for what a canonical row looks like.

For interfaces, types, file structure, see `README.md` in this directory.

## Rules specific to this lib

- Models are derived from the **live** `ithina_dis_db` schema (introspected), not the `schemas/postgres/` DDL files. Hand-aligned (no codegen yet); load-bearing fields carry an inline evidence comment citing the introspected column.
- Models here are the SHAPE. Adding a column means: (1) add to model here, (2) Alembic migration in repo-root `alembic/`, (3) update streaming-consumer mapping engine, (4) update dbt models in BQ.
- Never make a field optional that has a NOT NULL DB column **without a default**. DB-generated columns (`id` via `uuidv7()`, `last_updated_at`/`created_at` via `now()`, `regulatory_flag` default `false`) ARE Optional here. Tension: this single model serves both write (pre-insert, Optional correct) and read (always populated) paths; a Create/Row split is deferred until a consumer needs the non-null read guarantee.
- `mapping_version_id` is mandatory (required) on the three mapping-produced models (current_position, sale_events, change_events; D22). `store_sku_signal_history` has NO `mapping_version_id` — it is daily-compute output (D22/D31/D32); do not add one.
- Value-range and cross-field CHECK invariants (`>= 0`, `unit_sale_price <= unit_retail_price`, etc.) are NOT modelled here; they belong to the DB and to dis-validation (Slice 5).
- `TenantId`/`StoreId` come from `dis_core.identifiers` (internal UUID keys), not the identity contract's external string aliases (D37 split — see dis-core CLAUDE.md).

## References

- `README.md` (this directory) — interface and structure.
- Root `CLAUDE.md` — project-wide invariants.
