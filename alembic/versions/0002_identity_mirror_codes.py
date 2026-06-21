"""identity_mirror external codes: tenants.display_code, stores.store_code

Adds the two authoritative Customer Master external-code columns to the
identity mirror (decisions.md D55, Slice 9a): ``display_code`` on
``identity_mirror.tenants`` and ``store_code`` on ``identity_mirror.stores``.
Both nullable — the source columns are nullable in Customer Master (live
introspection, D55 as corrected). Copied as-is by Mirror Sync; readability
only, never a translation bridge (D37: the load-bearing identity is the UUID).

No backfill here: population is Mirror Sync's normal DB-pull run, whose
``IS DISTINCT FROM`` upsert clauses make the backfill idempotent and a no-op
against an empty mirror.

``IF NOT EXISTS`` / ``IF EXISTS`` on both directions because 0001 applies the
``schemas/postgres`` DDL files dynamically: a fresh database bootstraps the
columns from the updated manifest, and this migration must then be a no-op.

See: docs/slices/slice-09a-identity-correction.md, decisions.md D55/D37/D23.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-04

"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Target-safety guard (Slice 1 §A.2 pattern, copied from 0001). Expected DIS
# database name, with the Customer Master database hard-blocked regardless.
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"


def check_migration_target(current: str, *, expected_db: str = _EXPECTED_DB, cm_db: str = _CM_DB) -> None:
    """Pure target check: refuse Customer Master outright, require the DIS database.

    Split from the connection read so the refusal logic is unit-testable without
    a live bind (the non-skippable anchor for the target-safety criterion).
    """
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
    _guard_target()
    op.get_bind().exec_driver_sql(
        'ALTER TABLE identity_mirror.tenants ADD COLUMN IF NOT EXISTS display_code TEXT COLLATE "C"'
    )
    op.get_bind().exec_driver_sql(
        'ALTER TABLE identity_mirror.stores ADD COLUMN IF NOT EXISTS store_code TEXT COLLATE "C"'
    )
    op.get_bind().exec_driver_sql(
        "COMMENT ON COLUMN identity_mirror.tenants.display_code IS "
        "'Customer Master''s authoritative external tenant code (core.tenants.display_code, "
        "e.g. buc-ees). Copied as-is by Mirror Sync; nullable at source (D55). Readability "
        "only; the load-bearing identity is tenant_id (D37).'"
    )
    op.get_bind().exec_driver_sql(
        "COMMENT ON COLUMN identity_mirror.stores.store_code IS "
        "'Customer Master''s authoritative external store code (core.stores.store_code, "
        "e.g. TX-102). Copied as-is by Mirror Sync; nullable at source (D55). Readability "
        "only; the load-bearing identity is store_id (D37).'"
    )


def downgrade() -> None:
    _guard_target()
    op.get_bind().exec_driver_sql("ALTER TABLE identity_mirror.stores DROP COLUMN IF EXISTS store_code")
    op.get_bind().exec_driver_sql("ALTER TABLE identity_mirror.tenants DROP COLUMN IF EXISTS display_code")
