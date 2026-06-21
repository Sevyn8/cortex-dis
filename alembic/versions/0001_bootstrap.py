"""bootstrap: full DIS schema set from schemas/postgres DDL

Applies every DDL file under schemas/postgres/ verbatim, in dependency order,
as a manifest. The migration additionally authors only what no DDL file
declares: the schema creation, a centralized grants block for ithina_dis_user,
and the initial daily partitions for the partitioned parents. The SQL files
remain the source of truth for schema definition.

See: docs/slices/slice-01-bootstrap-migration.md, decisions.md D22/D33/D34,
CLAUDE.md hard rules 1/5/7.

Revision ID: 0001
Revises:
Create Date: 2026-06-02

"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Paths and manifest
# ---------------------------------------------------------------------------
# This file: alembic/versions/0001_bootstrap.py -> repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DDL_ROOT = _REPO_ROOT / "schemas" / "postgres"

# The 7 DIS schemas this migration creates (no DDL file issues CREATE SCHEMA).
SCHEMAS = [
    "audit",
    "bronze",
    "canonical",
    "config",
    "identity_mirror",
    "quarantine",
    "staging",
]

# DDL files applied verbatim, in dependency order. Binding constraints:
#   - FK targets first: identity_mirror.tenants -> stores -> config.source_mappings
#   - ENUM-type dependency: canonical/staging current_position defines
#     tax_treatment_enum/expiry_source_enum, used by the sibling sale_events,
#     so current_position must precede sale_events.
MANIFEST = [
    "00_extensions/uuidv7_setup.sql",
    "identity_mirror/tenants.sql",
    "identity_mirror/stores.sql",
    "config/source_mappings.sql",
    "canonical/store_sku_current_position.sql",
    "canonical/store_sku_sale_events.sql",
    "canonical/store_sku_change_events.sql",
    "canonical/store_sku_signal_history.sql",
    "staging/store_sku_current_position.sql",
    "staging/store_sku_sale_events.sql",
    "staging/store_sku_change_events.sql",
    "staging/store_sku_signal_history.sql",
    "bronze/data_ingress_events.sql",
    "audit/events.sql",
    "quarantine/quarantined_chunks.sql",
    "quarantine/quarantined_rows.sql",
]

# Partitioned parents -> partition key column. EMPTY: every parent is now
# plain for beta. audit.events left first (Slice 30a; D45 closed, D77); the 6
# canonical/staging event + signal_history parents followed (migration 0009;
# D77's scope clause consciously revised) — their DDL files declare the plain
# shape, so partitioning them here would fail. Partitioning + automation
# return at Slice 21 for all 7 tables.
PARTITIONED: list[tuple[str, str, str]] = []

APP_ROLE = "ithina_dis_user"

# Initial partition window: CURRENT_DATE - 1 .. CURRENT_DATE + 5 (7 daily).
_DAYS_BACK = 1
_DAYS_FORWARD = 5

# Target-safety guard (Slice 1 §A.2). Expected DIS database name, with the
# Customer Master database hard-blocked regardless.
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"


def _exec(sql: str) -> None:
    """Execute raw SQL via the DBAPI, bypassing SQLAlchemy text() bind parsing.

    The DDL files contain ':' / '::' sequences (e.g. ``::uuid`` casts in RLS
    policies) that text() would misread as bind parameters. exec_driver_sql
    sends the string straight to psycopg, which also supports the multi-
    statement DDL files.
    """
    op.get_bind().exec_driver_sql(sql)


def _guard_target() -> None:
    """Refuse to run against the wrong database. Protects both local (two
    Postgres instances: DIS on 5433, Customer Master on 5432) and cloud."""
    current = op.get_bind().exec_driver_sql("SELECT current_database()").scalar()
    if current == _CM_DB:
        raise RuntimeError(
            f"Refusing to run DIS migration: connected to the Customer Master "
            f"database '{current}'. Point POSTGRES_ADMIN_URL at the DIS database."
        )
    if current != _EXPECTED_DB:
        raise RuntimeError(
            f"Refusing to run DIS migration: connected to '{current}' but expected "
            f"DIS database '{_EXPECTED_DB}' (POSTGRES_DB). Check POSTGRES_ADMIN_URL."
        )


def _create_initial_partitions() -> None:
    """Create 7 daily partitions per each of the 6 partitioned parents,
    CURRENT_DATE-relative. IF NOT EXISTS for cloud-replay safety."""
    # cast() only — typing for the scalar() Any|None; SELECT CURRENT_DATE always
    # returns one date row, so the expression itself is untouched (slice-9d rule:
    # never rewrite a committed migration's expressions).
    start = cast(date, op.get_bind().exec_driver_sql("SELECT CURRENT_DATE").scalar())
    start = start - timedelta(days=_DAYS_BACK)
    span = _DAYS_BACK + _DAYS_FORWARD + 1  # inclusive -> 7 days
    for schema, table, _key in PARTITIONED:
        for i in range(span):
            day = start + timedelta(days=i)
            nxt = day + timedelta(days=1)
            pname = f"{table}_p{day.strftime('%Y%m%d')}"
            _exec(
                f'CREATE TABLE IF NOT EXISTS "{schema}"."{pname}" '
                f'PARTITION OF "{schema}"."{table}" '
                f"FOR VALUES FROM ('{day.isoformat()}') TO ('{nxt.isoformat()}')"
            )


def upgrade() -> None:
    # 0. Target-safety guard before any DDL.
    _guard_target()

    # 1. Schemas (no DDL file declares these).
    for schema in SCHEMAS:
        _exec(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    # 2. Grants — default privileges first, so every table/sequence created
    #    below (incl. partitions and the config BIGSERIAL sequence) inherits.
    #    No FOR ROLE clause: defaults bind to the migrating role (the creator).
    for schema in SCHEMAS:
        _exec(f'GRANT USAGE ON SCHEMA "{schema}" TO {APP_ROLE}')
        _exec(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
        )
        _exec(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT USAGE ON SEQUENCES TO {APP_ROLE}')

    # 3. Apply DDL manifest verbatim, in dependency order.
    for rel in MANIFEST:
        _exec((_DDL_ROOT / rel).read_text())

    # 4. Initial daily partitions for the 6 partitioned parents.
    _create_initial_partitions()

    # 5. Backstop grants for objects created by the manifest/partition steps
    #    (covers the config BIGSERIAL sequence and all parents/partitions).
    for schema in SCHEMAS:
        _exec(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO {APP_ROLE}')
        _exec(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{schema}" TO {APP_ROLE}')


def downgrade() -> None:
    # Same target-safety guard before any destructive DDL.
    _guard_target()

    # DROP SCHEMA ... CASCADE removes tables, partitions, types, policies,
    # views, triggers, and FKs in each schema. Order is irrelevant under CASCADE.
    for schema in SCHEMAS:
        _exec(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    # uuidv7() lives in public (not a DIS schema), so drop it explicitly.
    _exec("DROP FUNCTION IF EXISTS public.uuidv7()")
