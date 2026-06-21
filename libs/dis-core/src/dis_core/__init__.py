"""dis-core — DIS shared base types and primitives.

Foundational, dependency-light building blocks every DIS service and lib imports:

- ``errors`` — the single ``DisError``-rooted exception hierarchy (leaf-level).
- ``identifiers`` — internal UUID-key type aliases (``TenantId``/``StoreId``/
  ``TraceId``/``MappingVersionId``). See the collision warning in CLAUDE.md.
- ``ids`` — UUIDv7 generation (the only sanctioned generator; never ``uuid4``).
- ``trace_id`` — trace_id minting + context-local access.
- ``timestamps`` — UTC-only datetime helpers (never naive).
- ``logging`` — structured JSON logging binding service/stage/tenant_id/trace_id.
- ``bq`` — ``BqClient`` Phase-1 stub (inert; real client in Phase 3 / Slice 21).
- ``identity`` — the Identity Service client interface (Slice 2).
- ``pubsub_names`` — resolve a Pub/Sub topic/subscription SHORT name from env,
  defaulting to the frozen contract literal (lets infra override the deployed name).
"""
