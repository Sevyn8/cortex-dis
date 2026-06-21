# `services/identity-service/` — *v1.0*

The Tenant/Store Identity Service. Wraps the physically separate admin database, mediates access via a cache, publishes `identity.changed` events on admin DB writes.

**Purpose.** Be the single, cached, audited point of access between the data platform and the physically separate admin database for tenant/store resolution and validation.

**Entry.**
- Trigger: gRPC or REST request. Four interface methods (architecture §4.2): `resolve_from_token` (called by §3.1 receiver-api), `resolve_from_upload` (called by §3.2 csv-upload), `resolve_from_endpoint` (called by §3.3 csv-erp and §3.4 reverse-api), `validate` (called by §3.7 streaming-consumer).
- Inputs: vary per method (JWT, upload session ID, endpoint config ID, or tenant_id+store_id).
- Preconditions: caller authorized (mTLS or service-to-service token); cache or admin DB reachable.

**Process.**
- For resolve methods: check cache; on hit, return cached identity. On miss, query admin DB; populate cache with TTL; return.
- For `validate(tenant_id, store_id)`: lightweight cache lookup with fallback to admin DB; returns `(exists, is_active)`.
- On admin DB write (separate code path triggered by the admin app, not by these requests): publish `identity.changed` event to Pub/Sub (consumed by §3.6 mirror-sync-consumer).
- Stale-while-error: on admin DB error, serve cached entries up to 5 min stale; emit metric and alert.
- Emit audit event per cache miss and per admin DB query.

**Exit.**
- Success: response with identity payload (`{tenant_id, store_id, is_active, metadata, ...}`) or boolean for `validate`. Latency target: p50 < 5ms (cache hit), p95 < 50ms (cache miss with admin DB query).
- Side outputs: cache populated; on admin writes, `identity.changed` published (consumed by §3.6 mirror-sync-consumer).
- Failure modes handled: cache miss + admin DB down → serve stale (up to 5 min); cache miss + admin DB down + no stale entry → return error; circuit breaker open → return error.
- Failure modes propagated: callers handle error responses (receivers reject with 503; §3.7 streaming-consumer falls back to `identity_mirror` direct read maintained by §3.6).
- Edge case: tenant deactivated between cache TTL boundaries; stale window may allow up to 5 min of writes from a deactivated tenant. Acknowledged tradeoff; downstream RLS still scopes the data.


```
services/identity-service/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── identity_service/
│       ├── __init__.py
│       ├── main.py             # gRPC + REST server entrypoint
│       ├── config.py
│       │
│       ├── handlers/           # interface methods (per architecture §4.2)
│       │   ├── __init__.py
│       │   ├── resolve_from_token.py
│       │   ├── resolve_from_upload.py
│       │   ├── resolve_from_endpoint.py
│       │   └── validate.py
│       │
│       ├── cache/
│       │   ├── __init__.py
│       │   ├── store.py        # Redis or in-process LRU
│       │   ├── ttl.py          # TTL policy (5-15 min)
│       │   └── invalidator.py  # subscribe to identity.changed for cache evict
│       │
│       ├── admin_db/           # the only place admin DB credentials live
│       │   ├── __init__.py
│       │   ├── client.py
│       │   └── queries.py
│       │
│       ├── publisher/          # publish identity.changed on admin DB writes
│       │   ├── __init__.py
│       │   └── changed_events.py
│       │
│       └── health/             # stale-while-error logic
│           ├── __init__.py
│           └── circuit_breaker.py
│
├── tests/
│   ├── unit/
│   │   ├── test_cache.py
│   │   ├── test_ttl.py
│   │   └── test_resolve_handlers.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_resolve_cached.py
│   │   ├── test_resolve_miss.py
│   │   ├── test_stale_while_error.py
│   │   └── test_changed_event_publish.py
│   └── fixtures/
│       └── admin_db_seed.sql
│
├── scripts/
│   ├── run-local.sh
│   └── seed-admin-db.sh
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

**Why `handlers/` has four files instead of one.** Each resolve method has different inputs, different lookup paths in the admin DB, and different caching keys. Splitting them keeps each method's logic isolated and Claude Code can work on one without needing context on the others. `validate.py` is the FK-substitute check used by the streaming consumer; very different code path from resolve.

**Why `cache/` is its own folder.** Three concerns (storage backend, TTL policy, invalidation listener) that interact tightly. Grouping makes the cache substitutable: Redis today, in-process LRU as a frugal-v0 option, possibly Memorystore later. Same interface throughout.

**Why `admin_db/` is gated.** The architecture promise is that *only* the Identity Service touches the admin DB. The `admin_db/` folder is where that promise is enforced: any DB access is in this folder, the rest of the codebase imports from it but never opens connections directly. Easy to audit, easy to lock down with linting rules.

**Why `publisher/` exists.** Identity Service is the only thing that knows when admin data changes (because it mediates all writes). When it does, downstream caches and `identity_mirror` need to know. The publisher is the event source.

**What's deliberately not here.** No JWT verification logic (Customer Master does that; Identity Service receives an already-authenticated context). No RBAC (Customer Master's job). No user creation or admin-app logic (lives in the admin app, not here).

---
