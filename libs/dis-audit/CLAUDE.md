# libs/dis-audit — Claude Code Context

Loaded when Claude Code works in `libs/dis-audit/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

The audit-event model (`AuditEvent`), the Phase-1 Cloud SQL writer for `audit.events`
(fire-and-forget), the inert Phase-3 BigQuery seam, and the stage/scope/outcome vocabulary
consumers import. No service emits audit here — emission is service-layer (Slice 7 onward).

For interfaces, types, file structure, see `README.md`.

## Rules specific to this lib (Slice 6)

- **Derive the model from the live `audit.events`, not from D14/§8/the DDL/the BQ shape.**
  `AuditEvent` has one field per live column (24 since Slice 30c); the integration test reconciles the field
  set against `information_schema.columns` both directions as the drift guard.
- **`event_date` is derived from `event_timestamp` (UTC date), never caller-set** — so the live
  `ck_audit_events_event_date_matches` CHECK can't be violated. `id` and `_loaded_at` are
  server-defaulted (`uuidv7()` / `now()`) and omitted from the INSERT (`dis-core` `ids` sanctions
  the DB-default path for server-side PKs). `trace_id` is caller-supplied, never minted (hard rule 4).
- **Write posture goes through `dis-rls` `rls_session` (hard rule 12).** Inherits the
  `current_database()` target guard (5433 / `ithina_dis_db` only). Every audit event carries a
  known `tenant_id`; there is **no tenant-less audit path** (`decisions.md` D43). A `None` tenant
  raises `AuditWriteError` (logged), never a silent drop.
- **Fire-and-forget (hard rule 11) — the one sanctioned swallow (code-quality rule 6).** A write
  failure is logged with `tenant_id`/`trace_id`/`stage` and reported as `False`, never raised, never
  blocking the data path. A missing partition (`decisions.md` D45), missing grant, or schema
  mismatch is logged as **error worth alerting**, not absorbed as routine. Duplicates are tolerated
  (`decisions.md` D44); no dedup key.
- **`Stage` is a closed owned enum** (all Phase-1 stages; Phase-3 stages excluded). `EventScope` /
  `Outcome` mirror the live CHECK vocab exactly. `DUPLICATE_NOOP`/`DUPLICATE_OVERWRITTEN` are
  first-class `Outcome` members and `prior_trace_id` is a live column since Slice 30c (the D42
  REVISION: Slice 10's deliberate event_data-JSONB resolution superseded for console
  queryability); they REFINE SUCCESS (the append-only insert landed, D33). `row_hash`/`dedup_key`
  stay in `event_data`. `FailureCode` (Slice 30b, D79) is the closed failure vocabulary. The
  drift guard checks the full column shape (type/nullability/length) against
  `schema_contract.EXPECTED_COLUMNS`, not just names (Slice 30c).
- **BigQuery seam is inert** (import-safe, no I/O, imports no `google-cloud-bigquery`), behind
  `dis-core` `BqClient` (hard rule 8). No method body fleshed out. Mirrors the Slice 3 stub.
- **Depends on `dis-core` + `dis-rls` only.** Never `dis-mapping`/`dis-validation`/`dis-canonical`;
  `mapping_version_id` is a value the caller supplies. Never log PII or raw payloads.

## References

- `README.md` — interface and structure.
- Root `CLAUDE.md` — project-wide invariants. `decisions.md` D34, D42, D43, D44, D45.
