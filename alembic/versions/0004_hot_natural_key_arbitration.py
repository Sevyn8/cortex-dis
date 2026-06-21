"""hot natural key: COALESCE-sentinel arbiter index replaces the NND constraint (M-HOTKEY)

The autoscaling redesign (Slice 10 Part 3, D58 split): PG15 cannot arbitrate
ON CONFLICT against the NULLS NOT DISTINCT natural-key constraint when key
segments are NULL (verified empirically on the live 15.17), which forced a
single-instance-only read-modify-write upsert. This migration restores atomic
arbitration:

1. Precondition assert (only when the arbiter index is genuinely about to be
   created): no row carries ``sku_variant = ''`` or ``sku_lot_batch = ''`` —
   the operator-confirmed impossible sentinel. Violation aborts loudly; the
   single migration transaction rolls everything back.
2. Sentinel CHECKs FIRST (``<> ''`` on both nullable key segments): from this
   statement on the sentinel is ENGINE-impossible, not convention.
3. ``CREATE UNIQUE INDEX uq_sscp_natural_key`` over
   ``(tenant_id, store_id, sku_id, COALESCE(sku_variant,''),
   COALESCE(sku_lot_batch,''))`` — created only after the CHECKs hold and
   BEFORE the old constraint drops, so uniqueness is never gapped.
4. ``DROP CONSTRAINT uq_sscp_natural``.

Ordering invariant (operator confirm): whenever ``uq_sscp_natural_key``
exists, both CHECKs exist — upgrade adds CHECKs strictly before the index;
downgrade recreates the NND constraint first, then drops the index strictly
before the CHECKs. After a full downgrade ``''`` can enter (pre-redesign
semantics, uniqueness intact via NND); the re-upgrade's step-1 assert catches
exactly that intrusion.

Idempotent for 0001-fresh-bootstrap parity (the updated schemas/postgres
manifest already carries the CHECKs + index and no NND constraint): every step
is exists-checked, and the precondition assert is skipped when the index
already exists (the CHECKs then already make '' impossible).

Arbitration, two-writer safety, and the deadlock posture were proven on a
scratch 15.17 with THIS DDL verbatim (Slice 10 plan Part 3 §§2-4).

See: docs/decisions.md (D58 split, D63, D64), slice-10 plan Part 3/3a.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05

"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Target-safety guard (Slice 1 §A.2 pattern, copied from 0001/0002/0003).
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"

_TABLE = "canonical.store_sku_current_position"
_INDEX = "uq_sscp_natural_key"
_OLD_CONSTRAINT = "uq_sscp_natural"
_CHECKS = (
    ("ck_sscp_sku_variant_not_empty", "sku_variant <> ''"),
    ("ck_sscp_sku_lot_batch_not_empty", "sku_lot_batch <> ''"),
)


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


def _constraint_exists(name: str) -> bool:
    row = op.get_bind().exec_driver_sql("SELECT 1 FROM pg_constraint WHERE conname = %s", (name,)).scalar()
    return row is not None


def _index_exists(name: str) -> bool:
    row = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT 1 FROM pg_indexes WHERE schemaname = 'canonical' AND indexname = %s",
            (name,),
        )
        .scalar()
    )
    return row is not None


def _assert_no_empty_sentinels() -> None:
    """The sentinel precondition: '' never legitimately occurs in the key segments.

    Run only when the arbiter index is genuinely about to be created (a fresh
    bootstrap already carries the CHECKs, which make '' impossible). A violation
    means rows entered while the redesign was not in force (e.g. during a
    downgrade window) — abort loudly; never build a collision-prone sentinel
    index over them.
    """
    count = (
        op.get_bind()
        .exec_driver_sql(
            f"SELECT COUNT(*) FROM {_TABLE} WHERE sku_variant = '' OR sku_lot_batch = ''"  # noqa: S608
        )
        .scalar()
    )
    if count != 0:
        raise RuntimeError(
            f"Refusing to create {_INDEX}: {count} row(s) in {_TABLE} carry an "
            "empty-string sku_variant/sku_lot_batch — the COALESCE sentinel would "
            "collide. Resolve those rows (the value is never legitimate) and re-run."
        )


def upgrade() -> None:
    _guard_target()

    # 1. Precondition (only when the index is about to be created).
    if not _index_exists(_INDEX):
        _assert_no_empty_sentinels()

    # 2. Sentinel CHECKs FIRST — '' becomes engine-impossible before any index
    #    relies on it.
    for name, predicate in _CHECKS:
        if not _constraint_exists(name):
            op.get_bind().exec_driver_sql(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {name} CHECK ({predicate})")

    # 3. The arbiter index — after the CHECKs, before the old constraint drops
    #    (no uniqueness gap at any moment).
    op.get_bind().exec_driver_sql(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} ON {_TABLE} "
        "(tenant_id, store_id, sku_id, COALESCE(sku_variant, ''), COALESCE(sku_lot_batch, ''))"
    )

    # 4. Retire the NND constraint PG15 cannot arbitrate for NULL segments.
    op.get_bind().exec_driver_sql(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_OLD_CONSTRAINT}")

    op.get_bind().exec_driver_sql(
        f"COMMENT ON INDEX canonical.{_INDEX} IS "
        "'The hot-table natural key and the ON CONFLICT arbiter (M-HOTKEY, Slice 10 "
        "Part 3). COALESCE('''') sentinel because PG15 cannot arbitrate ON CONFLICT "
        "against a NULLS NOT DISTINCT index when key segments are NULL; '''' is "
        "engine-impossible via ck_sscp_sku_variant_not_empty / "
        "ck_sscp_sku_lot_batch_not_empty, so this key''s uniqueness domain equals the "
        "retired uq_sscp_natural. Concurrency-safe under N consumer instances "
        "(D58 split).'"
    )


def downgrade() -> None:
    _guard_target()
    # Mirror order: uniqueness never gapped, and the CHECKs outlive the index.
    if not _constraint_exists(_OLD_CONSTRAINT):
        op.get_bind().exec_driver_sql(
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_OLD_CONSTRAINT} "
            "UNIQUE NULLS NOT DISTINCT (tenant_id, store_id, sku_id, sku_variant, sku_lot_batch)"
        )
    op.get_bind().exec_driver_sql(f"DROP INDEX IF EXISTS canonical.{_INDEX}")
    for name, _predicate in _CHECKS:
        op.get_bind().exec_driver_sql(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {name}")
