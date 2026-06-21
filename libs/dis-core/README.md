# `libs/dis-core/`

Shared base types, IDs, errors, and small primitives used across every service.

```
libs/dis-core/
├── pyproject.toml
├── README.md
├── src/
│   └── dis_core/
│       ├── __init__.py
│       ├── errors.py           # DisError root + identity client errors (leaf-level)
│       ├── identifiers.py      # TenantId/StoreId/TraceId/MappingVersionId (internal UUID keys)
│       ├── ids.py              # UUIDv7 generation (new_uuid7)
│       ├── trace_id.py         # trace_id minting + context-local access
│       ├── timestamps.py       # UTC-only datetime helpers (now_utc, ensure_utc)
│       ├── logging.py          # structured JSON logging (service/stage/tenant_id/trace_id)
│       ├── bq.py               # BqClient Phase-1 stub (inert; real client in Phase 3)
│       └── identity/           # Identity Service client interface (Slice 2)
└── tests/
    └── unit/
```

`result.py` from the original sketch is intentionally not built — no current
consumer needs a Result type (build to need; a later slice adds it if warranted).
`TenantId`/`StoreId` here are UUID — and since Slice 9a so are the identity
contract's `tenant_id`/`store_id` fields in `identity/models.py` (D37 resolved;
the invented external `t_*`/`s_*` aliases are retired, D52).

**Why this lib exists.** Every service needs trace_id generation, error types, structured logging. Without a shared lib, each service invents its own and the codebase fragments. With a shared lib, the conventions are enforced by import.

**What's deliberately not here.** No business logic. No model definitions (those live in `dis-canonical`, `dis-mapping`). No I/O — the `BqClient` is an inert Phase-1 stub. Dependencies are kept minimal (pydantic, httpx, uuid-utils, python-json-logger).

---
