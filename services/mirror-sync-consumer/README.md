# `services/mirror-sync-consumer/` — *v1.0*

Maintains the `identity_mirror` schema in the data-platform Postgres so canonical tables can have real FKs against tenant and store identifiers.

**Two modes (see `decisions.md` D35).** Ships with two operational modes that share the same upsert path:
- **DB-pull mode** (v1.0 launch path): reads tenant/store records directly from Customer Master's Postgres database; upserts into `identity_mirror`. On-demand and schedulable. This is the only active mode at launch.
- **Pub/Sub consumer mode** (deferred): subscribes to `identity.changed` from Customer Master. Activates once Customer Master emits the events. Architecturally canonical; build-guide.md tracks the slice that turns it on.

DB-pull persists past launch as a reconciliation mechanism even after Pub/Sub goes live.

**Purpose.** Keep the data-platform's `identity_mirror` schema in sync with Customer Master, so canonical tables can enforce FK constraints against tenant and store identifiers without crossing the DB boundary.

**Entry — Pub/Sub mode (deferred).**
- Trigger: Pub/Sub message on `identity.changed` subscription. Producer: §3.5 identity-service.
- Inputs: event envelope `{event_type: created|updated|deactivated, entity: tenant|store, entity_id, payload, source_ts}`.
- Preconditions: data-platform Postgres reachable; subscription healthy.

**Process — Pub/Sub mode.**
- Receive event; ack-extend if processing time approaches deadline.
- Dispatch by `entity` type to the corresponding sync function (`sync/tenants.py` or `sync/stores.py`).
- For `created` and `updated`: upsert into `identity_mirror.tenants` or `stores` with `source_ts` as conflict-resolution key (older events don't overwrite newer).
- For `deactivated`: replicate Customer Master's `status` (no `is_active` column exists); never hard-delete (canonical rows may still reference).
- Emit audit event with `trace_id` derived from event metadata.
- Ack message on successful commit.

**Exit — Pub/Sub mode.**
- Success: mirror row upserted; ack on the message. No downstream emission. `identity_mirror` is read by §3.7 streaming-consumer FK pre-check and as a fallback when §3.5 identity-service is unreachable.
- Failure modes handled: Postgres transient error → nack (Pub/Sub retries with backoff); event out-of-order (older `source_ts` than current row) → ack and skip (no-op).
- Failure modes propagated: persistent Postgres failure → nack repeatedly until DLQ thresholds trigger; ops alerted.
- Edge case: `identity.changed` published before the corresponding admin DB write commits (identity-service ordering bug) — mirror sees a row that the next `resolve_from_*` cannot find. Mitigated by retry-with-backoff on the read side; the mirror eventually converges.

**Entry — DB-pull mode (v1.0 launch).**
- Trigger: on-demand CLI invocation or scheduled run (Cloud Scheduler in cloud; cron locally).
- Inputs: Customer Master DB connection (env-configured: port 5432 locally; Cloud SQL host in cloud).
- Preconditions: Customer Master DB reachable; read credentials present; data-platform Postgres reachable.

**Process — DB-pull mode.**
- Open a read-only session against Customer Master's Postgres.
- Read `tenants` and `stores` tables in full (or by `updated_at` watermark on subsequent runs).
- For each row: upsert into `identity_mirror.tenants` or `stores` using the same sync functions Pub/Sub mode uses (`sync/tenants.py`, `sync/stores.py`).
- Replicate Customer Master's `status` verbatim (upsert-only; never delete, never soft-delete — there is no `is_active` column).
- Log run start and run completion with per-tenant counts (structured logs). Audit is log-only this slice — no `audit.events` rows and no `dis-audit` dependency.

**Exit — DB-pull mode.**
- Success: identity_mirror reflects the current state of Customer Master tenants/stores; run summary logged with counts.
- Failure modes handled: Customer Master DB transient unreachable → retry with backoff; transient upsert conflict → retry the row.
- Failure modes propagated: persistent CM DB failure → exit non-zero; ops alerted.
- Edge case: a Customer Master row was deleted hard (not soft). DB-pull detects rows present in mirror but absent in source; flags them via audit but does not delete (canonical FKs may still reference). Ops reconciles manually.


```
services/mirror-sync-consumer/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── mirror_sync_consumer/
│       ├── __init__.py
│       ├── main.py             # Pub/Sub subscriber entrypoint
│       ├── config.py
│       │
│       ├── consumer/            # Pub/Sub mode (deferred until CM emits)
│       │   ├── __init__.py
│       │   ├── subscribe.py    # Pub/Sub pull loop
│       │   └── handler.py      # dispatch by event type
│       │
│       ├── pull/                # DB-pull mode (v1.0 launch)
│       │   ├── __init__.py
│       │   ├── runner.py       # CLI / scheduler entrypoint
│       │   ├── reader.py       # reads CM Postgres tenants + stores
│       │   └── reconcile.py    # flags drift between mirror and source
│       │
│       ├── sync/               # shared upsert logic for both modes
│       │   ├── __init__.py
│       │   ├── tenants.py      # upsert tenants
│       │   └── stores.py       # upsert stores (lifecycle via status)
│       │
│       └── sinks/
│           ├── __init__.py
│           └── postgres.py     # writes to identity_mirror schema
│
├── tests/
│   ├── unit/
│   │   ├── test_handler_dispatch.py
│   │   ├── test_tenants_sync.py
│   │   └── test_stores_sync.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_create_event.py
│   │   ├── test_update_event.py
│   │   └── test_soft_delete_event.py
│   └── fixtures/
│       └── events/             # sample identity.changed events
│
├── scripts/
│   ├── run-local.sh
│   └── replay-events.sh        # replay events from a saved snapshot
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

**Why this service is small.** It does one thing: keep `identity_mirror` aligned with Customer Master. Whether triggered by a Pub/Sub event or a DB pull, the write path is the same. The architecture intent matters more than the code volume; this service is what makes the FK contract from canonical to `identity_mirror` work in practice.

**Why two modes.** Customer Master does not yet emit `identity.changed`. The Pub/Sub design is the architectural target (see `architecture.md` §4.3 and `decisions.md` D11). DB-pull mode lets DIS development proceed without blocking on Customer Master's emit work. Both modes call the same `sync/` upsert path so behavior is identical from `identity_mirror`'s perspective. DB-pull persists as reconciliation even after Pub/Sub goes live.

**Why `sync/` is split by entity (tenants, stores).** Different tables, different schema, different columns and lifecycle. Splitting makes each clear and testable.

**Why `pull/` has a `reader.py` reading Customer Master Postgres directly.** DB-pull mode is the only place in DIS that reads Customer Master's DB schema. This coupling is intentional and bounded to one file (`reader.py`). When Pub/Sub mode activates, the reader can stay (reconciliation use case) or be deprecated; either way, the impact is contained.

**What's deliberately not here.** No identity resolution logic (the Identity Service does that). This service never serves identity lookups; it only keeps the mirror current.

---
