# `services/quarantine-drainer/` — *v1.0*

Subscribes to the `quarantine` Pub/Sub topic, writes failed rows/chunks to the Cloud SQL `quarantine` schema with all context needed for the DIS UI to display.

**Purpose.** Take the streaming consumer's `quarantine` Pub/Sub messages and persist them to the `quarantine.*` tables with enough context for tenants to understand failures and for ops to investigate.

**Entry.**
- Trigger: Pub/Sub message on `quarantine` subscription. Producers: §3.7 streaming-consumer (post-validation failures), §3.2 csv-upload phase 2 (preflight failures), §3.3 csv-erp phase 2 (preflight failures).
- Inputs: event envelope with `{trace_id, tenant_id, source_id, failure_type, failure_context, original_row_or_chunk, bronze_ref, parent_trace_id?}`.
- Preconditions: Cloud SQL `quarantine` schema reachable; subscription healthy.

**Process.**
- Receive event; ack-extend if needed.
- Dispatch by `failure_type` (source-shape, normalization, canonical-shape, FK) to the corresponding sink writer.
- Enrich: resolve `parent_trace_id` lineage if present (for resubmit chains); generate Pandera/GE suite-failure documentation link if applicable.
- Open RLS-aware transaction scoped to `tenant_id`; insert into `quarantine.quarantined_chunks` (chunk-level failures) or `quarantine.quarantined_rows` (row-level failures); commit.
- Emit audit event for the quarantine write.
- Ack message.

**Exit.**
- Success: quarantine row persisted; ack on Pub/Sub. Rows are read by §3.10 dis-ui-server quarantine handler for tenant and ops display.
- Failure modes handled: Cloud SQL transient error → nack (Pub/Sub retries with backoff); duplicate event (same `trace_id` + `row_hash`) → idempotent insert (no-op); message malformed → DLQ to `quarantine.dlq` for ops.
- Failure modes propagated: persistent Cloud SQL failure → DLQ; ops alerted.
- Edge case: large failure batch from a single chunk (e.g., 10,000-row CSV with all rows failing canonical-shape) — batched insert in chunks of N to avoid long transactions.


```
services/quarantine-drainer/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── quarantine_drainer/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       │
│       ├── consumer/
│       │   ├── __init__.py
│       │   ├── subscribe.py
│       │   └── handler.py      # dispatch by failure type
│       │
│       ├── enrichment/         # add context the streaming consumer didn't include
│       │   ├── __init__.py
│       │   ├── lineage.py      # parent_trace_id resolution
│       │   └── suite_link.py   # link to suite failure docs (Pandera output)
│       │
│       └── sinks/
│           ├── __init__.py
│           └── postgres.py     # write to quarantine.* tables (RLS)
│
├── tests/
│   ├── unit/
│   │   ├── test_handler_dispatch.py
│   │   ├── test_lineage.py
│   │   └── test_postgres_sink.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_source_shape_failure.py
│   │   ├── test_normalization_failure.py
│   │   ├── test_canonical_shape_failure.py
│   │   └── test_idempotency.py
│   └── fixtures/
│       └── failures/           # sample quarantine messages by type
│
├── scripts/
│   ├── run-local.sh
│   └── replay-quarantine.sh
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

**Why `enrichment/` here too.** Same pattern as receivers: the drainer adds context the producer (streaming consumer) didn't have time to compute. `parent_trace_id` resolution requires looking up the original chunk's trace; suite link generation requires resolving the failed expectation to a docs URL. Both are enrichment.

**Why `handler.py` dispatches by failure type.** Source-shape, normalization, canonical-shape, and FK failures have different schemas and different routing rules in the quarantine table. Per-type handlers keep each clean.

**What's deliberately not here.** No replay logic (`tools/replay/` handles that). No alerting (that's a separate concern, lives in observability glue). No quarantine UI (that's in DIS UI; this is just the writer to the quarantine table).

---
