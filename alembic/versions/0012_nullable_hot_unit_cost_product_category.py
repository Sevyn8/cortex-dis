"""nullable hot columns: unit_cost, product_category on store_sku_current_position — Slice 16j

Makes the hot table more permissive at ingest: a catalogue/snapshot row may land
without ``unit_cost`` or ``product_category`` (both become NULLABLE), and downstream
decides how to handle the absence. Last slice of the nullable-columns arc (16h made
the write gate model-derived; 16i subtracted enrichment-guaranteed columns; 16j flips
these two model fields Optional). Because the create gate, the write-time completeness
gate, and the catalog mandatory flag all derive their required set from the canonical
model (``mandatory_mapping_produced``, keyed on ``is_required()``), the relaxation
auto-propagates from the Optional model change with no gate/catalog edit.

- upgrade(): two ``ALTER COLUMN … DROP NOT NULL`` (inline SQL; never re-reads the DDL
  files at runtime). ``ck_sscp_unit_cost_non_negative`` is LEFT INTACT — a bare SQL
  CHECK evaluates to UNKNOWN on NULL, which Postgres treats as not-violated, so it stays
  valid (and correct) once the column is nullable. No other column, partition, role, or
  policy is touched.
- downgrade(): two ``ALTER COLUMN … SET NOT NULL`` restoring the pre-slice form. Real
  leg (mirrors 0011); the downgrade ROUND-TRIP test is @pytest.mark.skip per D99
  (downgrade-reversibility testing deferred until staging).

Fresh == migrated: 0001 applies the edited DDL files verbatim (already nullable), so a
fresh bootstrap to head lands the identical nullability the delta path leaves (the
DROP NOT NULL is a no-op on a fresh DB). Proven by test_migration_0012.py.

See: docs/slices/slice-16j-nullable-canonical-columns.md, decisions.md D99 (downgrade
defer), D101/D102 (16h/16i model-derived gates), the 0011 fresh==migrated precedent.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-20

"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Target-safety guard (Slice 1 §A.2 pattern, copied from 0001..0011).
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"

_TABLE = "canonical.store_sku_current_position"
# The two columns this slice relaxes; the only schema mutation in either direction.
_COLUMNS = ("unit_cost", "product_category")


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
    """Raw DBAPI execution (the DDL idiom shared with 0001/0011)."""
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _guard_target()
    # Drop NOT NULL on exactly the two target columns. The non-negative CHECK on
    # unit_cost is NULL-safe and stays — no constraint is dropped here.
    for column in _COLUMNS:
        _exec(f"ALTER TABLE {_TABLE} ALTER COLUMN {column} DROP NOT NULL")


def downgrade() -> None:
    _guard_target()
    # Restore the pre-slice NOT NULL. Fails loudly if NULL rows exist by then — the
    # inherent downgrade risk, deferred under D99 (the round-trip test is skipped).
    for column in _COLUMNS:
        _exec(f"ALTER TABLE {_TABLE} ALTER COLUMN {column} SET NOT NULL")
