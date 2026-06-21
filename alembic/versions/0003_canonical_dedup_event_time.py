"""canonical event-table dedup columns + hot event-time reference (D38, D64)

Resolves D38 (Slice 10 prerequisite, M-D38/D64): the D33 read-time latest-wins
dedup key ``(tenant_id, store_id, source_id, source_event_id)`` named columns
absent from the applied canonical schema. Adds to BOTH event tables:

- ``source_id VARCHAR(128) COLLATE "C" NOT NULL`` — type, length, and collation
  introspected to match ``config.source_mappings.source_id`` and
  ``bronze.data_ingress_events.source_id`` (both ``varchar(128)`` collation C),
  so mapping-lookup and dedup keys compare and index consistently.
- ``source_event_id VARCHAR(256) COLLATE "C" NOT NULL`` — per-source event id;
  composed max is transaction_id(128) + ':' + line_item_seq(<=6) = 135; the D65
  fallback (bronze_ref UUID 36 + ':' + chunk_row_index) is 47. 256 is headroom.

plus the window-supporting index per parent, and ``last_source_event_at
TIMESTAMPTZ NULL`` on ``store_sku_current_position`` (D64: the event-time-wins
comparison reference; NULL = never event-written, e.g. pre-seeded rows).

NOT NULL with no DEFAULT is legal only against empty tables (both event tables
introspected at 0 rows, slice-10 plan). The emptiness is re-checked HERE,
immediately before each ADD, and only when the column is genuinely about to be
added — because 0001 applies the ``schemas/postgres`` DDL files dynamically, a
fresh database bootstraps these columns from the updated manifest and this
migration must then be a no-op (the 0002 idempotency pattern).

See: docs/slices/slice-10-streaming-consumer.md, decisions.md D38/D33/D64/D65.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-05

"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Target-safety guard (Slice 1 §A.2 pattern, copied from 0001/0002). Expected
# DIS database name, with the Customer Master database hard-blocked regardless.
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"

# (table, event-time column for the dedup-window index, index name,
#  per-table source_event_id population text — sale and change differ)
_SALE_SEID_COMMENT = (
    "Sale events use transaction_id || '':'' || line_item_seq when the source "
    "supplies them; otherwise the deterministic fallback "
    "bronze_ref || '':'' || chunk_row_index (redelivery-stable, NOT "
    "correction-collapsing; D65)."
)
_CHANGE_SEID_COMMENT = (
    "Change events carry no native source event-id column, so the deterministic "
    "fallback bronze_ref || '':'' || chunk_row_index applies (redelivery-stable, "
    "NOT correction-collapsing; D65)."
)
_EVENT_TABLES = (
    (
        "canonical.store_sku_sale_events",
        "source_sale_timestamp",
        "ix_ssse_dedup_key",
        _SALE_SEID_COMMENT,
    ),
    (
        "canonical.store_sku_change_events",
        "source_event_timestamp",
        "ix_ssce_dedup_key",
        _CHANGE_SEID_COMMENT,
    ),
)
_NEW_EVENT_COLUMNS = (
    ('source_id VARCHAR(128) COLLATE "C"', "source_id"),
    ('source_event_id VARCHAR(256) COLLATE "C"', "source_event_id"),
)


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


def _column_exists(qualified_table: str, column: str) -> bool:
    schema, table = qualified_table.split(".", 1)
    row = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
            (schema, table, column),
        )
        .scalar()
    )
    return row is not None


def _assert_empty(qualified_table: str, column: str) -> None:
    """The NOT NULL ADD (no DEFAULT) is legal only against an empty table.

    Re-checked immediately before each add (plan-time row counts can stale);
    a non-empty table here means the plan's precondition broke — abort loudly,
    the single migration transaction rolls everything back.
    """
    count = op.get_bind().exec_driver_sql(f"SELECT COUNT(*) FROM {qualified_table}").scalar()
    if count != 0:
        raise RuntimeError(
            f"Refusing to ADD {column} NOT NULL: {qualified_table} has {count} rows "
            f"(expected 0 per the M-D38/D64 plan precondition). Resolve and re-run."
        )


def upgrade() -> None:
    _guard_target()

    for table, ts_column, index_name, seid_comment in _EVENT_TABLES:
        for column_ddl, column_name in _NEW_EVENT_COLUMNS:
            if not _column_exists(table, column_name):
                _assert_empty(table, column_name)
                op.get_bind().exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column_ddl} NOT NULL"
                )
        op.get_bind().exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} "
            f"(tenant_id, store_id, source_id, source_event_id, {ts_column} DESC)"
        )
        op.get_bind().exec_driver_sql(
            f"COMMENT ON COLUMN {table}.source_id IS "
            "'Source registration identifier of the originating source. Matches "
            "config.source_mappings.source_id and bronze.data_ingress_events.source_id "
            "(varchar(128) COLLATE C, introspected). Component of the D33 read-time dedup "
            "key (tenant_id, store_id, source_id, source_event_id). Consumer-injected from "
            "the ingress.ready envelope, cross-checked against the GCS path and the bronze "
            "row (D38 resolution).'"
        )
        op.get_bind().exec_driver_sql(
            f"COMMENT ON COLUMN {table}.source_event_id IS "
            f"'Per-source event identifier completing the D33 dedup key. {seid_comment} "
            "Consumer-injected (D38 resolution).'"
        )
        # Reconciliation: the prior comment enumerated source_event_id as an
        # ingest_metadata JSONB key; it is a first-class column from 0003 on.
        op.get_bind().exec_driver_sql(
            f"COMMENT ON COLUMN {table}.ingest_metadata IS "
            "'JSONB diagnostic and lineage detail: source_name, source_event_timestamp, "
            "dis_received_timestamp, dis_published_timestamp, csv_row_num. Designed to "
            "evolve. (source_event_id moved to a first-class column, D38/0003.)'"
        )

    op.get_bind().exec_driver_sql(
        "ALTER TABLE canonical.store_sku_current_position "
        "ADD COLUMN IF NOT EXISTS last_source_event_at TIMESTAMPTZ"
    )
    op.get_bind().exec_driver_sql(
        "COMMENT ON COLUMN canonical.store_sku_current_position.last_source_event_at IS "
        "'Source event timestamp of the last event-table row merged into this hot row. "
        "Comparison reference for the event-time-wins conditional upsert (architecture "
        "2.3.1, D64). NULL = never event-written (e.g. pre-seeded catalogue rows). "
        "Consumer-injected by the streaming consumer.'"
    )


def downgrade() -> None:
    _guard_target()
    op.get_bind().exec_driver_sql(
        "ALTER TABLE canonical.store_sku_current_position DROP COLUMN IF EXISTS last_source_event_at"
    )
    for table, _ts_column, index_name, _seid_comment in _EVENT_TABLES:
        op.get_bind().exec_driver_sql(f"DROP INDEX IF EXISTS canonical.{index_name}")
        op.get_bind().exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN IF EXISTS source_event_id")
        op.get_bind().exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN IF EXISTS source_id")
        # Restore the pre-0003 ingest_metadata comment (the JSONB key again
        # carries the source event id once the first-class column is gone).
        op.get_bind().exec_driver_sql(
            f"COMMENT ON COLUMN {table}.ingest_metadata IS "
            "'JSONB diagnostic and lineage detail: source_name, source_event_id, "
            "source_event_timestamp, dis_received_timestamp, dis_published_timestamp, "
            "csv_row_num. Designed to evolve.'"
        )
