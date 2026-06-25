"""atlas.schema_drafts: the Atlas console draft/published IR store — A4 PR2

Creates the 8th schema, ``atlas``, and ``atlas.schema_drafts``: the platform-scoped
(non-RLS) store for the Atlas console's draft and frozen canonical-schema IRs
(written by dis-ui-server via rls_platform_session; decisions.md D104).

- upgrade(): CREATE SCHEMA atlas (no DDL file issues CREATE SCHEMA); grants to the
  app role (the 0001 grants idiom: default privileges first, then apply the DDL file
  verbatim, then a backstop grant); apply schemas/postgres/atlas/schema_drafts.sql
  (the table + the partial unique index + the immutability trigger function/trigger +
  the non-RLS rationale). The atlas DDL is applied ONLY here (0001's manifest is
  frozen and does not include atlas), so CREATE FUNCTION/CREATE TRIGGER run exactly
  once on any upgrade-to-head: no collision, and fresh==migrated holds for the
  function and trigger as well as the table.
- downgrade(): DROP SCHEMA atlas CASCADE (real leg; removes the table, index,
  trigger, and function). The downgrade ROUND-TRIP test is skipped per D99
  (downgrade-reversibility deferred until staging), as for 0011/0012.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-25

"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# Target-safety guard (Slice 1 §A.2 pattern, copied from 0001..0012).
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"
_APP_ROLE = "ithina_dis_user"
_SCHEMA = "atlas"
_DDL = Path(__file__).resolve().parents[1].parent / "schemas" / "postgres" / "atlas" / "schema_drafts.sql"


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
    current = op.get_bind().exec_driver_sql("SELECT current_database()").scalar()
    check_migration_target(str(current))


def _exec(sql: str) -> None:
    """Raw DBAPI execution (the DDL idiom shared with 0001..0012)."""
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _guard_target()
    # 1. The schema (no DDL file declares it).
    _exec(f'CREATE SCHEMA IF NOT EXISTS "{_SCHEMA}"')
    # 2. Grants — default privileges first, so the table created below inherits.
    _exec(f'GRANT USAGE ON SCHEMA "{_SCHEMA}" TO {_APP_ROLE}')
    _exec(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{_SCHEMA}" '
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE}"
    )
    # 3. Apply the DDL file verbatim (table + partial unique index + immutability trigger).
    _exec(_DDL.read_text())
    # 4. Backstop grant for the table created by the manifest step.
    _exec(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{_SCHEMA}" TO {_APP_ROLE}')


def downgrade() -> None:
    _guard_target()
    # Real leg: drop the schema and everything in it (table, index, trigger, function).
    # The round-trip test is skipped per D99 (downgrade-reversibility deferred).
    _exec(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
