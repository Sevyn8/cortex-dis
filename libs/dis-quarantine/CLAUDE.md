# libs/dis-quarantine — Claude Code Context

Loaded when Claude Code works in `libs/dis-quarantine/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

The quarantine record models (`QuarantinedChunk` / `QuarantinedRow`), the fail-loud
Cloud SQL writer for the live `quarantine.*` tables, and the `failure_stage`
vocabulary + audit-`Stage` mapping. No service decides WHAT to quarantine here —
the allowlist lives at the call site (streaming-consumer in Slice 11a).

For interfaces, types, file structure, see `README.md`.

## Rules specific to this lib (Slice 11a)

- **Fail-loud — the deliberate ASYMMETRY with dis-audit.** Audit is the record of
  what happened (fire-and-forget, hard rule 11); quarantine is the held thing
  itself — the data path. A failed write RAISES `QuarantineWriteError` (dis-core)
  so the caller NACKS; swallowing here is ack-and-lose, a data-loss bug. Do not
  copy dis-audit's swallow into this lib.
- **Write posture goes through `dis-rls` `rls_session` (hard rules 1/12).** Both
  tables are FORCE-RLS with the `tenant_isolation` policy; every record carries a
  known `tenant_id` (model-enforced, the D43-shaped contract). `hold_rows` lands
  one chunk's rows in ONE transaction and refuses mixed tenants and empty input.
- **Write grain is `status=NEW` only.** `id` / `status` / `last_updated_at` are
  server-defaulted and omitted from the INSERT; the lifecycle columns are NOT
  model fields, so a transition cannot be expressed here (a later slice).
- **Derive from the live tables, not the DDL headers.** The headers predate 11a
  (they attribute writes to the 11b drainer and describe a partial-success rows
  model that does not exist yet — both flagged in the register, not patched).
  The CHECK vocabularies are mirrored in model validators (`dis_channel`,
  `failure_stage`, the rows-table failure_stage SUBSET) so violations fail at the
  model with context, never as opaque CHECK errors at the INSERT.
- **`failure_reason` is the stable `FailureCode` member (D79)** — never an
  exception class name. Variable detail rides `failure_context` JSONB at
  column/check/reason grain; NEVER cell values, NEVER raw payload (the raw row
  stays in GCS, located by `gcs_uri` + `row_offset`).
- **Depends on `dis-core` + `dis-rls` + `dis-audit` only** (dis-audit for the
  `Stage`/`FailureCode` vocabulary — the D78/D79 seam). Never `dis-mapping` /
  `dis-validation` / `dis-canonical`; `mapping_version_id` is a value the caller
  supplies. Never log PII or raw payloads.
- **No Pub/Sub here.** The `quarantine` topic + drainer is Slice 11b; this lib is
  the INSERT path both designs share.

## References

- `README.md` — interface and structure.
- Root `CLAUDE.md` — project-wide invariants. `decisions.md` D78/D79 (the seam),
  D43/D44 (audit posture, the contrast), hard rule 11.
- `schemas/postgres/quarantine/quarantined_{chunks,rows}.sql` — the live DDL.
