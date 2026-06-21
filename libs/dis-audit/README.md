# `libs/dis-audit/`

Audit-event model, the Phase-1 Cloud SQL writer for `audit.events`, the inert Phase-3
BigQuery seam, and the stage/scope/outcome vocabulary its service consumers import.

```
libs/dis-audit/
├── pyproject.toml
├── README.md
├── src/
│   └── dis_audit/
│       ├── __init__.py
│       ├── event.py            # AuditEvent Pydantic model (one field per live audit.events column)
│       ├── stages.py           # owned vocabulary: Stage (closed), EventScope, Outcome
│       ├── writer.py           # AuditWriter interface + select_writer (backend selection)
│       ├── postgres_writer.py  # PostgresAuditWriter — Cloud SQL audit.events (Phase 1, active)
│       └── bigquery_writer.py  # BigQueryAuditWriter — inert Phase-3 seam (no I/O)
└── tests/
    └── unit/
```

**Why this lib exists.** Every service emits audit events with the same shape to the same
destination. The model, writer, and vocabulary belong in one shared lib so consumers
(Slices 8–18) import a stable surface instead of redefining stage/outcome strings.

**Where audit lands.** Phase 1: Cloud SQL `audit.events` (`decisions.md` D34). The writer
goes through `dis-rls` (`audit.events` is FORCE-RLS), is fire-and-forget (hard rule 11),
and requires a known `tenant_id` (`decisions.md` D43). BigQuery archival is Phase 3
(Slice 21); `BigQueryAuditWriter` is an inert seam behind `dis-core` `BqClient` until then.

No service emits audit events from this lib — emission is service-layer (Slice 7 onward).

---
