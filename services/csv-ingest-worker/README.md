# `services/csv-ingest-worker/` — *v1.0 (Slice 9b)*

The event-triggered worker for CSV upload (Phase 2 of the two-phase CSV ingress flow, D36).
Triggered by the `csv.received` event dis-ui-server publishes once a tenant's signed-PUT
upload is confirmed saved in GCS (D54 — **not** by a raw GCS object-finalize). Runs the
DuckDB structural preflight, wires the dis-pii fail-loud gate, persists one metadata-only
bronze row, and hands off to the streaming consumer via `ingress.ready`.

Phase 1 of CSV upload (upload session, identity resolution to UUID + codes, `trace_id`
minting, signed-URL issuance, tier-0 validation per D51) lives in `services/dis-ui-server/`
as the `upload_session` handler (Slice 8). See `decisions.md` D36 for the split, D54 for the
trigger model.

**Purpose.** Do the post-upload work that should not run inside a UI request: structural
preflight, the PII gate, bronze metadata persistence, and pipeline handoff. Scales with data
volume rather than UI concurrency.

**Entry.**
- Trigger: Pub/Sub `csv.received` (frozen contract, `contracts/pubsub/csv.received.schema.json`),
  pulled from the `csv-ingest-worker.csv.received` subscription (provisioned locally by
  `make topics-create`; the worker NEVER creates its own subscription — absence is a loud
  startup error).
- The event carries the **already-resolved internal identity** (UUID `tenant_id`/`store_id`),
  the external codes, `trace_id`, the upload session id, and the GCS pointer. The worker
  **trusts the event** (D54): it calls no Identity Service, performs no external-to-internal
  translation, and mints no `trace_id`.
- Preconditions: the object at `gcs_uri` is finalized; the path follows the canonical scheme
  (UUID tenant segment, D53).

**Process (per event).**
1. Parse the envelope (typed against the frozen contract; violations raise `EventContractError`).
2. Cross-check the GCS path against the event: `split_object_uri` + `parse_object_path`
   (dis-storage, hard rule 9) must agree with the event on bucket, tenant, source, trace, ext.
   A mismatch is a malformed PRODUCER (`EventPathMismatchError`) — a consistency check, never
   a re-resolution.
3. Download the object (dis-storage) and compute its SHA-256.
4. Idempotency: same `(tenant, upload_session_id, payload_sha256)` within 24h of the prior
   row's `received_at` → return the prior `trace_id`. PUBLISHED or FAILED prior → full no-op.
   Unpublished RECEIVED prior → **resume-and-mark** (D59): complete the lost publish under
   the prior trace, stamp `published_at`, write no second row.
5. DuckDB structural preflight (D13/D16, contained in `preflight.py` with canary tests):
   parses-as-CSV, header present, ≥1 column, ≥1 data row, type sniff. Structural ONLY —
   column-/mapping-aware checks are Slice 10's source-shape suite. Failure → bronze row with
   `processing_status='FAILED'`, audit FAILURE, **no publish**, ack (terminal).
6. PII gate (hard rule 2, D40): the sniffed header names pass through dis-pii's fail-loud
   gate BEFORE the bronze write. No per-column PII flag exists in the live schema (D40
   limitation 2) so only heuristic name detection can fire; v1.0 has no backend, so a
   detected column always raises (`PiiBackendNotConfiguredError`).
7. Bronze write: ONE metadata-only row via `dis-rls` under the event's tenant (hard rules
   1 & 12; FORCE RLS + the `current_database()=='ithina_dis_db'` target guard).
8. Publish the frozen `ingress.ready` envelope (hard rule 10) — **write-then-CONDITIONALLY-
   publish** (D5): only after bronze lands, and only on preflight success — then stamp
   `published_at`/`PUBLISHED`.
9. Audit at each stage (RECEIVED / PII_TOKENIZED / BRONZE_WRITTEN / INGRESS_PUBLISHED;
   idempotent no-op = RECEIVED + SKIPPED), fire-and-forget (hard rule 11, D43, D44).

**Exit.**
- Success: bronze row `PUBLISHED`; `ingress.ready` published (consumed by the streaming
  consumer, Slice 10); audit rows in `audit.events`; message acked.
- Failure modes:
  - *Terminal (contract/content):* malformed envelope, path mismatch, preflight failure,
    detected PII with no backend. FAILURE audit emitted, message ACKed (a redelivery would
    fail identically). Preflight failure leaves a durable `FAILED` bronze row; no quarantine
    path exists at this stage (quarantine is Slice 10/11's, for semantic failures).
  - *Transient (infrastructure):* DB/GCS/publish unreachable. Logged and NACKed; Pub/Sub
    redelivers and the idempotency path converges (D59).

**The two `received_ts` values (read this before consuming both events).**
`csv.received.received_ts` is the PRODUCER's timestamp (when dis-ui-server confirmed the GCS
save). `ingress.ready.received_ts` is when **DIS durably accepted** the chunk — the bronze
row's `received_at`, stamped by this worker. They are NOT the same instant; a downstream
reader of both must not assume they coincide. The dedup window is likewise measured against
bronze `received_at`, never the producer's timestamp.

```
services/csv-ingest-worker/
├── CLAUDE.md
├── README.md
├── pyproject.toml
│
├── src/csv_ingest_worker/
│   ├── __init__.py
│   ├── main.py          # entrypoint: wire deps, require the subscription, run the loop
│   ├── config.py        # required env (no silent defaults) + frozen contract constants
│   ├── envelope.py      # csv.received typed model + drift guard vs the contract file
│   ├── subscriber.py    # pull loop; terminal->ack, transient->nack
│   ├── pipeline.py      # per-event orchestration (the stage order above)
│   ├── preflight.py     # DuckDB containment (the ONLY module importing duckdb)
│   ├── pii_gate.py      # dis-pii wired over the sniffed header (synthetic rename shape)
│   ├── bronze.py        # dedup lookup, metadata-only INSERT, publish mark
│   ├── publisher.py     # ingress.ready model + drift guard; emulator-guarded publisher
│   └── audit.py         # per-stage fire-and-forget emission
│
└── tests/
    ├── unit/            # envelope/config/preflight+canary/pii/bronze/pipeline/subscriber
    │                    # + trust-boundary proofs (no identity import, no trace mint)
    ├── integration/     # live stack: e2e delivery, idempotency both ways, target safety
    └── fixtures/csvs/   # well-formed / headerless / header-only samples
```

**Why this is a worker, not a receiver.** No HTTP surface, no caller to authenticate (D36):
queue consumer, scales with backlog, retries via redelivery + idempotency.

**Why this service does not generate `trace_id`.** dis-ui-server mints it at Phase 1 and
carries it on `csv.received` (D54). This worker reads it; a test makes the minting function
explode to prove nothing here calls it.

**Why there is no identity client here.** The event is the trust boundary (D54). dis-ui-server
resolved identity at Phase 1; re-resolving would only re-derive what it already knew.
Freshness (tenant deactivated between upload and processing) is the streaming consumer's
validate, not this worker's. A test asserts `dis_core.identity` is never imported.

**Concurrency note (D58).** The idempotency check is query-based (no UNIQUE constraint over
the dedup key — a 24h window cannot be a plain unique index). Correct for a SINGLE worker
instance; scaling to concurrent instances requires a constraint/upsert design first.

**What's deliberately not here.** No identity resolution (Slice 13). No `csv.received`
publishing (Slice 8). No mapping, Pandera suites, canonical writes, `mapping_version_id`,
or quarantine routing (Slice 10/11). No PII tokenizer/key-vault/flag mechanism (D40's
deadline). No cloud notification wiring (deferred infra). No batching/retry tuning beyond
what the trigger contract requires.

---
