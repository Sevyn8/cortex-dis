# `services/receiver-csv-erp/` — *deferred (not in v1.0)*

The HTTP receiver for the per-tenant ERP POST endpoint. Same shape as `csv-ingest-worker/` but bound to a different auth model: ERP systems push via long-lived machine credentials (per-tenant API key or mTLS), not user sessions.

**Purpose.** Accept scheduled CSV batches from tenant ERP systems via machine credentials, using the same signed-URL pattern as csv-upload to avoid transiting large files.

**Entry.** Two distinct triggers (two-phase flow, same shape as csv-upload).
- *Phase 1 trigger:* HTTP POST to `/tenant/{tenant_id}/erp` from a tenant's ERP. Inputs: source_id, optional batch metadata, machine credential (API key in header, or mTLS client cert). Preconditions: credential valid for the named tenant; tenant + source registered; per-tenant rate limit not exceeded.
- *Phase 2 trigger:* Pub/Sub `bucket.objects.changed` event when the ERP completes the PUT to the signed URL.

**Process.**
- *Phase 1 (handler):* validate machine credential (API key or mTLS via `auth/` module); check per-tenant rate limit (token bucket); resolve identity via §3.5 identity-service `resolve_from_endpoint` method (identity is bound to the credential's endpoint config); generate `trace_id`; issue a signed PUT URL via `libs/dis-storage`; return.
- *Phase 2 (notification handler):* same as csv-upload phase 2 — preflight, PII tokenization, bronze write, `ingress.ready` publish, audit events.

**Exit.**
- *Phase 1 success:* HTTP 2xx with `{upload_url, trace_id, expires_at}`.
- *Phase 2 success:* same durable outputs as csv-upload (§3.2): bronze metadata row, `ingress.ready` published (consumed by §3.7 streaming-consumer), audit events.
- Phase 1 failure modes: 401 (bad credential), 403 (credential valid but not for this tenant), 429 (per-tenant rate limit exceeded; the receiver's first line of B3 performance-isolation defense), 503 (Identity Service circuit open).
- Phase 2 failure modes: identical to csv-upload; preflight failures route to `quarantine` topic (consumed by §3.8 quarantine-drainer).
- Edge case: same as csv-upload (signed URL expiry).


```
services/receiver-csv-erp/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── receiver_csv_erp/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       │
│       ├── handlers/
│       │   ├── __init__.py
│       │   └── erp_post.py     # POST /tenant/{tenant_id}/erp: issues signed URL
│       │
│       ├── notifications/      # GCS object-finalized event handler
│       │   ├── __init__.py
│       │   └── handler.py
│       │
│       ├── auth/               # ERP-specific auth (machine credentials)
│       │   ├── __init__.py
│       │   ├── api_key.py      # per-tenant API key validation
│       │   └── mtls.py         # mTLS cert validation
│       │
│       ├── enrichment/
│       │   ├── __init__.py
│       │   ├── identity.py     # call Identity Service resolve_from_endpoint
│       │   ├── trace.py
│       │   └── pii.py
│       │
│       ├── preflight/
│       │   ├── __init__.py
│       │   ├── duckdb_check.py
│       │   └── rules.py
│       │
│       ├── ratelimit/          # per-tenant rate limit (architecture B3 fix #1)
│       │   ├── __init__.py
│       │   └── token_bucket.py
│       │
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── bronze.py       # write metadata row (post-notification)
│       │   └── pubsub.py
│       │
│       └── clients/
│           ├── __init__.py
│           └── identity.py
│
├── tests/
│   ├── unit/
│   │   ├── test_auth_api_key.py
│   │   ├── test_auth_mtls.py
│   │   ├── test_ratelimit.py
│   │   ├── test_preflight.py
│   │   └── test_notification_handler.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_erp_signed_url.py
│   │   ├── test_object_finalized_flow.py
│   │   ├── test_erp_throttled.py
│   │   └── test_erp_auth_failure.py
│   └── fixtures/
│       └── csvs/
│
├── scripts/
│   ├── run-local.sh
│   └── post-local.sh
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

**Why two phases (same as csv-upload).** ERP batches can be large (15-30 min worth of inventory and sale events per store). Direct-to-GCS via signed URL avoids transiting bytes through the receiver; the notification handler does the post-upload work.

**Why `auth/` is its own folder here but not in other receivers.** ERP receivers handle two distinct machine-auth methods (API key, mTLS) chosen per tenant. The selection logic, key rotation, and certificate validation are non-trivial. Receiver-api uses bearer tokens via Customer Master (one method); CSV upload uses sessions (one method). ERP has two and the choice is per-tenant, so it earns its own folder.

**Why `ratelimit/` lives here.** Architecture B3 (performance isolation) requires per-tenant rate limits at the receiver. ERP is the highest-volume push channel (15-30 min batches per store, summed across tenants), so the rate limit lands here first. Same module pattern will be added to other receivers; this is the reference implementation. Rate limit applies to the signed-URL issue path (handler), not to the notification path (which fires once per successful upload).

**Why `identity.py` calls `resolve_from_endpoint`.** ERP identity is bound to the endpoint config (per-tenant URL or API key registration). The endpoint config knows which tenant + store the credentials map to; that's the lookup.

**What's deliberately not here.** No user-session handling; this is machine-driven. No DIS UI integration; ERP systems call this directly. No mapping (streaming consumer). No `sinks/gcs.py` (tenant uploads directly via signed URL).

---
