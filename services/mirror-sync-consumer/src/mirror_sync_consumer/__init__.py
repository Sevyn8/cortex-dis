"""mirror-sync-consumer — keep identity_mirror aligned with Customer Master.

Slice 7 builds **DB-pull mode** only: a finite, run-to-completion process that reads
Customer Master's Postgres (``core.tenants`` / ``core.stores``) under a platform read
context and upserts into ``identity_mirror.tenants`` / ``identity_mirror.stores``. The
same run serves first load and periodic reconciliation. Customer Master is the source of
truth; the mirror is a read-derived replica, never written back.

Two Postgres instances: it **reads** Customer Master (``CM_DB_URL``; read-only role under
``app.user_type='PLATFORM'``) and **writes** DIS (``POSTGRES_URL``; ``ithina_dis_user``).
Each connection asserts its target before any write (see ``pull.reader`` / ``sinks.postgres``).

Upsert-only: insert + update on natural keys, **never delete, never soft-delete**; lifecycle
is Customer Master's ``status`` replicated verbatim (there is no DIS-side ``is_active`` flag).

The Pub/Sub consumer mode (``identity.changed``) is deferred (``decisions.md`` D35) and is not
scaffolded here. Audit is **log-only** this slice (no ``audit.events`` rows; no ``dis-audit`` dep).
"""
