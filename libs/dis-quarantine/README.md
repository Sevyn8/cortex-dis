# libs/dis-quarantine

Quarantine record models + the fail-loud Cloud SQL writer for the two live tables
`quarantine.quarantined_chunks` and `quarantine.quarantined_rows`, plus the
`failure_stage` vocabulary their CHECKs enforce (Slice 11a).

The model (one line): **audit records what happened (write-only log, fire-and-forget);
quarantine holds what failed so it can be recovered (acted-on store, fail-loud).**
One deterministic failure produces both — they correlate by `trace_id` +
`data_ingress_event_id` (the D78 seam).

## Interface

```python
from dis_quarantine import (
    PostgresQuarantineWriter,   # hold_chunk(record) / hold_rows(records) — RAISES on failure
    QuarantinedChunk,           # one held ingress event (chunk-level deterministic failure)
    QuarantinedRow,             # one held data row (gate-level row failure; gcs_uri+row_offset locate it)
    QuarantineFailureStage,     # the live ck_q*_failure_stage_vocab members
    ROW_FAILURE_STAGES,         # the quarantined_rows CHECK subset
    failure_stage_for,          # total mapping: dis_audit.Stage -> QuarantineFailureStage
)
```

- `hold_chunk` / `hold_rows` write under `dis_rls.rls_session(engine, tenant_id)`
  (FORCE-RLS tables; hard rules 1/12) and raise
  `dis_core.errors.QuarantineWriteError` on ANY failure — the caller must nack,
  never ack-and-lose. `hold_rows` lands one chunk's rows in one transaction.
- Records are the WRITE shape only: `status` (DB default `'NEW'`), `id`,
  `last_updated_at` are server-stamped; the lifecycle columns
  (`resolution_note`, `resolved_at`, `resolved_by_user_id`) are not expressible —
  transitions are a later slice.
- `failure_reason` carries the stable `dis_audit.FailureCode` member (D79);
  variable detail rides `failure_context` JSONB (column/check/reason grain —
  NEVER cell values; the raw row stays in GCS, located by `gcs_uri` + `row_offset`).

## Structure

```
src/dis_quarantine/
├── __init__.py          # exports
├── failure_stages.py    # QuarantineFailureStage, ROW_FAILURE_STAGES, failure_stage_for
├── records.py           # QuarantinedChunk / QuarantinedRow (write-grain models)
└── postgres_writer.py   # PostgresQuarantineWriter (fail-loud, rls_session)
tests/unit/              # model invariants, INSERT shape, stage-mapping totality
```

## Who writes through it

- Slice 11a: `services/streaming-consumer` (direct write + ACK — the storm stopper).
- Later: `services/csv-ingest-worker` (its own deterministic classes) and the 11b
  drainer (the topic-mediated path) reuse the same write functions.

Depends on `dis-core` (errors, timestamps, logging), `dis-rls` (session), and
`dis-audit` (the `Stage` / `FailureCode` vocabulary — the D78/D79 seam).
