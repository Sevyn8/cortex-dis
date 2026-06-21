# `services/receiver-api/` — *deferred (not in v1.0)*

The HTTP receiver for API/webhook ingress: tenant POS systems and partner webhooks push events here. Synchronous accept, async transform: the receiver validates auth, enriches with identity, persists to bronze + GCS, publishes to `ingress.ready`, and returns 2xx. The pipeline does the actual work asynchronously.

**Purpose.** Accept structured event payloads from authenticated tenant systems and hand them off to the pipeline with full traceability and zero raw PII downstream.

**Entry.**
- Trigger: HTTP POST to `/ingest` or `/webhook/{partner_id}`.
- Inputs: JSON payload; bearer token (Customer Master-issued) or partner-specific signing scheme; source identifier in URL or token claims.
- Preconditions: caller authenticated; tenant and source registered; rate limit not exceeded.

**Process.**
- Validate bearer token / signature; reject 401 on failure.
- Resolve identity via `identity-service` (§3.5) `resolve_from_token` method (architecture §4.2); cache hit on hot path.
- Generate `trace_id`; attach to context for all downstream emit.
- Tokenize PII fields per `libs/dis-pii` policy (HMAC, per-tenant key) before any persistence.
- Persist raw payload to GCS at `tenant/{id}/source/{id}/yyyy=Y/mm=M/dd=D/{trace_id}.json` via `libs/dis-storage` (path scheme enforced by lib).
- Write enriched metadata row to bronze Postgres (no payload, just index columns).
- Publish `ingress.ready` Pub/Sub message with bronze pointer and identity context.
- Emit audit event for each stage (auth, identity, gcs write, bronze write, pubsub publish).

**Exit.**
- Success: HTTP 2xx with `{trace_id}` returned. Durable outputs: one GCS object, one bronze metadata row, one `ingress.ready` Pub/Sub message (consumed by §3.7 streaming-consumer), five audit events (read by §3.10 dis-ui-server audit handler).
- Failure modes handled here: 401 (bad auth), 400 (malformed payload), 404 (unknown source), 429 (rate limit), 503 (Identity Service circuit open or Pub/Sub publish failed). `trace_id` returned on every response including failures.
- Failure modes propagated: pipeline-side validation, mapping, and canonical write failures are not the receiver's concern; the receiver's job ends when `ingress.ready` is published. Failures downstream surface to operators via §3.8 quarantine-drainer and to tenants via the §3.10 quarantine handler.


```
services/receiver-api/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── receiver_api/
│       ├── __init__.py
│       ├── main.py             # HTTP server entrypoint (FastAPI / Litestar)
│       ├── config.py
│       │
│       ├── handlers/           # one HTTP handler per endpoint
│       │   ├── __init__.py
│       │   ├── ingest.py       # POST /ingest (API push)
│       │   └── webhook.py      # POST /webhook/{partner_id}
│       │
│       ├── enrichment/         # attach identity + trace_id to incoming chunk
│       │   ├── __init__.py
│       │   ├── identity.py     # call Identity Service resolve_from_token
│       │   ├── trace.py        # trace_id generation and propagation
│       │   └── pii.py          # PII tokenization (HMAC, per-tenant key)
│       │
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── gcs.py          # write raw payload to bronze GCS
│       │   ├── bronze.py       # write metadata row to bronze Postgres
│       │   └── pubsub.py       # publish ingress.ready message
│       │
│       └── clients/
│           ├── __init__.py
│           ├── identity.py     # Identity Service client
│           └── customer_master.py  # token validation (where machine auth applies)
│
├── tests/
│   ├── unit/
│   │   ├── test_enrichment.py
│   │   ├── test_pii_tokenization.py
│   │   └── test_handlers.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_happy_path.py
│   │   ├── test_auth_failures.py
│   │   └── test_idempotency.py
│   └── fixtures/
│       └── payloads/
│
├── scripts/
│   ├── run-local.sh
│   └── replay-request.sh       # curl-shaped local request helper
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

**Why `handlers/` instead of `pipeline/`.** Receivers don't transform; they accept and route. The verb is "handle a request," not "process a chunk." Different concept; different folder name.

**Why `enrichment/` is its own folder.** Identity attachment, trace generation, and PII tokenization are three distinct concerns that all happen in the receiver pre-write. Grouping them keeps the handler thin and makes each concern testable in isolation. PII tokenization especially needs its own file: it is the most safety-critical code in the receiver, and isolating it makes the audit posture cleaner.

**Why `sinks/` has three files for what looks like one operation.** GCS write, bronze Postgres write, and Pub/Sub publish are not atomic. They have different failure modes (GCS quota, Postgres lock, Pub/Sub backpressure) and different ordering requirements (GCS before bronze before Pub/Sub). Splitting them makes the order explicit and each failure mode handlable. The handler orchestrates the three; the sinks don't know about each other.

**Why `customer_master.py` is in `clients/`.** Machine auth (API key validation, mTLS) may flow through Customer Master depending on which scope is settled. Today it's DIS-internal; the client wrapper exists so a future switch to Customer Master doesn't require changing handler code. Read the v0.3 callout in the architecture doc for context.

**What's deliberately not here.** No mapping logic (that's the streaming consumer's job). No validation beyond structural (auth + parseable payload). No queue beyond Pub/Sub. The receiver's job is "accept and route," not "decide and transform."

---
