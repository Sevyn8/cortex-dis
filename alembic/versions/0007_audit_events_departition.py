"""audit.events: de-partition (remove the D45 silent write-cliff) — Slice 30a

audit.events was PARTITION BY RANGE (event_date) with a fixed bootstrap-created
daily window (live: events_p20260601..events_p20260607), no DEFAULT partition,
and no partition automation (decisions.md D45). Past the last partition every
audit write errors "no partition found", which the dis-audit writer's
fire-and-forget swallows (hard rule 11) — audit silently stops recording.

This migration converts audit.events to a PLAIN table for beta:

- upgrade(): drop the partitioned table (children drop with the parent; the
  rows are disposable — operator-confirmed storm junk, no preservation) and
  re-apply schemas/postgres/audit/events.sql verbatim (the manifest pattern,
  as 0001). The DDL file is the source of truth and now declares the plain
  shape: PK (id) — the composite (id, event_date) existed only to satisfy the
  partition-key-in-PK requirement — with every other column, CHECK (including
  ck_audit_events_event_date_matches, which defines event_date's semantics and
  is Slice 21's re-partition invariant), the FK to identity_mirror.tenants,
  the five secondary indexes, and the FORCE RLS policy unchanged.
- downgrade(): recreate the partitioned form (frozen pre-30a shape, inlined
  below) with a FRESH 7-day partition window (CURRENT_DATE-1 .. +5, 0001's
  logic) — NOT the original 2026-06-01..07 dates. Rows are not preserved in
  either direction.

Fresh == migrated by construction: a fresh bootstrap applies the same plain
events.sql at 0001 (whose PARTITIONED list no longer includes audit.events),
then this migration re-applies it; a migrated DB gets it applied here.

Partitioning, with automation, returns at Slice 21 (BQ archive + eviction,
decisions.md D29/D34). The audit writer needs no change (its INSERT targets
the parent table; partitioning was transparent).

See: docs/slices/slice-30a-audit-departition.md, decisions.md D45/D34/D29/D43/D44.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-06

"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


# This file: alembic/versions/0007_audit_events_departition.py -> repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DDL_FILE = _REPO_ROOT / "schemas" / "postgres" / "audit" / "events.sql"

APP_ROLE = "ithina_dis_user"

# Downgrade partition window: CURRENT_DATE - 1 .. CURRENT_DATE + 5 (7 daily), as 0001.
_DAYS_BACK = 1
_DAYS_FORWARD = 5

# Target-safety guard (0001 §A.2 pattern). Expected DIS database name, with the
# Customer Master database hard-blocked regardless.
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"


def _exec(sql: str) -> None:
    """Execute raw SQL via the DBAPI, bypassing SQLAlchemy text() bind parsing.

    The DDL file contains ':' / '::' sequences (e.g. ``::UUID`` casts in the RLS
    policy) that text() would misread as bind parameters. exec_driver_sql sends
    the string straight to psycopg, which also supports multi-statement DDL.
    """
    op.get_bind().exec_driver_sql(sql)


def check_migration_target(current: str, *, expected_db: str = _EXPECTED_DB, cm_db: str = _CM_DB) -> None:
    """Pure target check: refuse Customer Master outright, require the DIS database."""
    if current == cm_db:
        raise RuntimeError(
            f"Refusing to run DIS migration: connected to the Customer Master "
            f"database '{current}'. Point POSTGRES_ADMIN_URL at the DIS database."
        )
    if current != expected_db:
        raise RuntimeError(
            f"Refusing to run DIS migration: connected to '{current}' but expected "
            f"DIS database '{expected_db}' (POSTGRES_DB). Check POSTGRES_ADMIN_URL."
        )


def _guard_target() -> None:
    """Refuse to run against the wrong database. Protects both local (two
    Postgres instances: DIS on 5433, Customer Master on 5432) and cloud."""
    current = op.get_bind().exec_driver_sql("SELECT current_database()").scalar()
    check_migration_target(str(current))


def upgrade() -> None:
    # 0. Target-safety guard before any DDL.
    _guard_target()

    # 1. Drop the partitioned table; its partitions drop with the parent.
    #    Nothing FK-references audit.events. Rows are disposable (slice doc).
    _exec("DROP TABLE IF EXISTS audit.events")

    # 2. Re-apply the DDL file verbatim (manifest pattern, as 0001): the plain
    #    table, constraints, indexes, FORCE RLS + policy, comments.
    _exec(_DDL_FILE.read_text())

    # 3. Backstop grant (0001 step-5 precedent). The audit-schema default ACLs
    #    already cover tables created by the migrating role; explicit anyway.
    _exec(f"GRANT SELECT, INSERT, UPDATE, DELETE ON audit.events TO {APP_ROLE}")


# The frozen pre-30a partitioned shape, for downgrade only. Matches the live
# pre-30a table introspected on 5433 (constraints/indexes/RLS verbatim).
_PARTITIONED_DDL = """
CREATE TABLE audit.events (
    id                          UUID                                NOT NULL DEFAULT uuidv7(),
    event_timestamp             TIMESTAMPTZ                         NOT NULL,
    event_date                  DATE                                NOT NULL,
    trace_id                    UUID                                NOT NULL,
    tenant_id                   UUID                                NULL,
    data_ingress_event_id       UUID                                NULL,
    service_name                VARCHAR(64)  COLLATE "C"            NOT NULL,
    service_version             VARCHAR(64)  COLLATE "C"            NULL,
    stage                       VARCHAR(64)  COLLATE "C"            NOT NULL,
    event_scope                 VARCHAR(32)  COLLATE "C"            NOT NULL,
    outcome                     VARCHAR(32)  COLLATE "C"            NOT NULL,
    row_count                   INTEGER                             NULL,
    rows_succeeded              INTEGER                             NULL,
    rows_failed                 INTEGER                             NULL,
    duration_ms                 INTEGER                             NULL,
    row_offset                  INTEGER                             NULL,
    mapping_version_id          BIGINT                              NULL,
    failure_code                VARCHAR(64)  COLLATE "C"            NULL,
    failure_message             VARCHAR(2048)                       NULL,
    event_data                  JSONB                               NULL,
    auth_principal              VARCHAR(256) COLLATE "C"            NULL,
    client_ip                   INET                                NULL,
    _loaded_at                  TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_audit_events
        PRIMARY KEY (id, event_date),

    CONSTRAINT fk_audit_events_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id)
        ON DELETE RESTRICT,

    CONSTRAINT ck_audit_events_event_scope_vocab
        CHECK (event_scope IN ('INGRESS_EVENT', 'ROW')),

    CONSTRAINT ck_audit_events_outcome_vocab
        CHECK (outcome IN ('SUCCESS', 'FAILURE', 'SKIPPED', 'RETRIED')),

    CONSTRAINT ck_audit_events_row_count_non_negative
        CHECK (row_count IS NULL OR row_count >= 0),

    CONSTRAINT ck_audit_events_rows_succeeded_non_negative
        CHECK (rows_succeeded IS NULL OR rows_succeeded >= 0),

    CONSTRAINT ck_audit_events_rows_failed_non_negative
        CHECK (rows_failed IS NULL OR rows_failed >= 0),

    CONSTRAINT ck_audit_events_duration_non_negative
        CHECK (duration_ms IS NULL OR duration_ms >= 0),

    CONSTRAINT ck_audit_events_event_date_matches
        CHECK (event_date = (event_timestamp AT TIME ZONE 'UTC')::DATE)
)
PARTITION BY RANGE (event_date);

CREATE INDEX ix_audit_events_trace_id
    ON audit.events (trace_id);

CREATE INDEX ix_audit_events_tenant_time
    ON audit.events (tenant_id, event_timestamp DESC)
    WHERE tenant_id IS NOT NULL;

CREATE INDEX ix_audit_events_service_stage_time
    ON audit.events (service_name, stage, event_timestamp DESC);

CREATE INDEX ix_audit_events_data_ingress_event
    ON audit.events (data_ingress_event_id)
    WHERE data_ingress_event_id IS NOT NULL;

CREATE INDEX ix_audit_events_failures
    ON audit.events (tenant_id, event_timestamp DESC)
    WHERE outcome = 'FAILURE';

ALTER TABLE audit.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.events FORCE ROW LEVEL SECURITY;

CREATE POLICY rls_audit_events_tenant
    ON audit.events
    USING (
        tenant_id = current_setting('app.tenant_id', true)::UUID
        OR tenant_id IS NULL
    );
"""


def downgrade() -> None:
    # Same target-safety guard before any destructive DDL.
    _guard_target()

    # Recreate the partitioned form with a FRESH window (CURRENT_DATE-relative,
    # 0001's logic) — not the original 2026-06-01..07 dates. No row preservation.
    _exec("DROP TABLE IF EXISTS audit.events")
    _exec(_PARTITIONED_DDL)

    start = cast(date, op.get_bind().exec_driver_sql("SELECT CURRENT_DATE").scalar())
    start = start - timedelta(days=_DAYS_BACK)
    span = _DAYS_BACK + _DAYS_FORWARD + 1  # inclusive -> 7 days
    for i in range(span):
        day = start + timedelta(days=i)
        nxt = day + timedelta(days=1)
        pname = f"events_p{day.strftime('%Y%m%d')}"
        _exec(
            f'CREATE TABLE IF NOT EXISTS "audit"."{pname}" '
            f'PARTITION OF "audit"."events" '
            f"FOR VALUES FROM ('{day.isoformat()}') TO ('{nxt.isoformat()}')"
        )

    _exec(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA audit TO {APP_ROLE}")
