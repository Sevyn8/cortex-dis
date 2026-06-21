"""config.source_mappings: template_type discriminator (Slice 14d)

The single DDL of Slice 14d: a stored ``template_type`` formalises the implicit
sale-vs-change discriminator (previously column-subset inference at load) into a
column read by the field catalog, the rule-target validator, and the streaming
consumer's routing. It is the spine that lets a catalogue / current-position
snapshot template route directly into the hot table beside the event packets.

Column shape (register decision at the commit gate):

- **TEXT, code-enforced vocabulary, NO enum type and NO CHECK.** The vocabulary
  (``snapshot`` / ``sales`` / ``inventory_change``) lives in exactly ONE place in
  code — ``dis_validation.TEMPLATE_TYPES`` — read by every consumer of it. A DB
  enum or a baked CHECK list would be a SECOND copy of the vocabulary, and the
  vocabulary is slated to move to a lookup table when it stabilises (decision 4);
  a DB-side enumeration now would have to be migrated again then. Enforcement is
  at the application boundary (create/edit reject a non-member), exactly as the
  slice specifies.
- **NOT NULL after backfill.** ``template_type`` is stored, not inferred (slice
  principle): every row carries one. The 3-step shape (add nullable → backfill →
  SET NOT NULL) is required because the table has live rows.

Backfill — formalising the current inference WITHOUT importing the libs into the
migration: a row's type is whichever family its rule TARGETS fit (rename values +
derive keys), checked against the disjoint signature columns of each event model
(the same disjointness the live ``route_target_model`` relies on). A legacy /
empty mapping that carries no recognisable signature (e.g. the
``manual_csv_upload`` bootstrap seed, whose rename/derive are empty and which
produces NO canonical contribution at all) defaults to ``sales`` — the
default-upload channel's family; the label is inert for a mapping that produces
nothing, and no ``snapshot`` (catalogue) row exists pre-14d, so ``snapshot`` is
never a backfill outcome.

Idempotent for 0001-fresh-bootstrap parity (the updated
``schemas/postgres/config/source_mappings.sql`` manifest carries the full end
state: the column NOT NULL and exposed by the view): the ADD COLUMN is gated on
column existence; the backfill only touches rows still NULL (none on a fresh DB);
SET NOT NULL is idempotent; CREATE OR REPLACE VIEW and the COMMENTs are natively
idempotent. The view's ``template_type`` is APPENDED last (CREATE OR REPLACE VIEW
cannot reorder or drop existing columns) — the manifest matches that order.

Downgrade drops ``template_type`` and recreates the pre-14d view (the SELECT list
without it).

See: docs/slices/slice-14d-catalogue-ingestion-front-door.md.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-08

"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Target-safety guard (Slice 1 §A.2 pattern, copied from 0005/0007).
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"

# Disjoint signature columns per event family (rename values OR derive keys).
# Drawn from the live mapping-produced sets of StoreSkuSaleEvent /
# StoreSkuChangeEvent; the two sets never co-occur in one mapping (the same
# disjointness route_target_model relies on).
_SALE_SIGNATURE = (
    "source_sale_timestamp",
    "transaction_id",
    "line_item_seq",
    "quantity",
    "unit_retail_price",
    "unit_sale_price",
)
_CHANGE_SIGNATURE = (
    "source_event_timestamp",
    "event_category",
    "attribute_name",
    "value_before",
    "value_after",
)


def _pg_array(values: tuple[str, ...]) -> str:
    """A Postgres text[] literal from a fixed tuple of identifier strings."""
    inner = ", ".join(f"'{v}'" for v in values)
    return f"ARRAY[{inner}]::text[]"


# The view's full end-state definition (template_type APPENDED last, after the
# computed label, so CREATE OR REPLACE VIEW neither reorders nor drops columns).
# Byte-aligned with schemas/postgres/config/source_mappings.sql.
_VIEW_WITH_TEMPLATE_TYPE = """\
CREATE OR REPLACE VIEW config.source_mappings_v WITH (security_invoker = true) AS
SELECT
    sm.mapping_version_id,
    sm.tenant_id,
    sm.source_id,
    sm.version_seq_per_source,
    sm.status,
    sm.mapping_rules,
    sm.pre_validation_suite_ref,
    sm.post_validation_suite_ref,
    sm.predecessor_version_id,
    sm.activated_at,
    sm.deprecated_at,
    sm.created_by_user_id,
    sm.created_at,
    sm.metadata,
    LOWER(REGEXP_REPLACE(SPLIT_PART(t.name, ' ', 1), '[^a-zA-Z0-9]', '', 'g'))
        || '-' || sm.source_id
        || '-v' || sm.version_seq_per_source
        || '-' || TO_CHAR(sm.created_at, 'YYYYMMDD')
        AS label,
    sm.template_type
FROM config.source_mappings sm
JOIN identity_mirror.tenants t
    ON t.tenant_id = sm.tenant_id;
"""

# Pre-14d view (no template_type), restored on downgrade. A plain CREATE (the
# downgrade DROPs the view first): CREATE OR REPLACE cannot DROP the trailing
# template_type column, only append.
_VIEW_PRE_0010 = """\
CREATE VIEW config.source_mappings_v WITH (security_invoker = true) AS
SELECT
    sm.mapping_version_id,
    sm.tenant_id,
    sm.source_id,
    sm.version_seq_per_source,
    sm.status,
    sm.mapping_rules,
    sm.pre_validation_suite_ref,
    sm.post_validation_suite_ref,
    sm.predecessor_version_id,
    sm.activated_at,
    sm.deprecated_at,
    sm.created_by_user_id,
    sm.created_at,
    sm.metadata,
    LOWER(REGEXP_REPLACE(SPLIT_PART(t.name, ' ', 1), '[^a-zA-Z0-9]', '', 'g'))
        || '-' || sm.source_id
        || '-v' || sm.version_seq_per_source
        || '-' || TO_CHAR(sm.created_at, 'YYYYMMDD')
        AS label
FROM config.source_mappings sm
JOIN identity_mirror.tenants t
    ON t.tenant_id = sm.tenant_id;
"""

_COMMENT = (
    "Mapping template packet axis (Slice 14d): snapshot | sales | inventory_change. "
    "Stored, not inferred. The vocabulary lives once in code "
    "(dis_validation.TEMPLATE_TYPES), read by the field catalog, the rule-target "
    "validator, and the streaming consumer's routing; no DB enum/CHECK (a lookup-table "
    "move is deferred). Backfilled from the rule-target signature; legacy/empty "
    "mappings defaulted to sales."
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


def _exec(sql: str) -> None:
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _guard_target()

    # 1. Add the column (nullable) if absent — skipped on a fresh-bootstrap DB
    #    where the manifest already created it NOT NULL.
    _exec(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'config'
                  AND table_name = 'source_mappings'
                  AND column_name = 'template_type'
            ) THEN
                ALTER TABLE config.source_mappings ADD COLUMN template_type TEXT COLLATE "C" NULL;
            END IF;
        END
        $$;
        """
    )

    # 2. Backfill rows still NULL (the delta path) by the rule-target signature.
    #    A row's target set = rename VALUES (the canonical columns it produces)
    #    plus derive KEYS. Change-signature is checked first; the two are
    #    disjoint, so order is immaterial for real rows, and ELSE catches the
    #    legacy/empty mapping (defaulted to sales — see module docstring).
    #    NOTE (operator-accepted, commit gate): this backfill + its ELSE were
    #    exercised only on LOCAL test rows. The production Cloud SQL target is
    #    empty and template_type is an additive column, so the backfill runs over
    #    zero real rows and the ELSE never fires on production data. (Recorded in
    #    the slice doc / decisions at commit.)
    sale_sig = _pg_array(_SALE_SIGNATURE)
    change_sig = _pg_array(_CHANGE_SIGNATURE)
    _exec(
        f"""
        WITH targets AS (
            SELECT
                sm.mapping_version_id AS mv,
                COALESCE(
                    (SELECT array_agg(v) FROM jsonb_each_text(sm.mapping_rules -> 'rename') AS r(k, v)),
                    ARRAY[]::text[]
                )
                || COALESCE(
                    (SELECT array_agg(k) FROM jsonb_object_keys(sm.mapping_rules -> 'derive') AS d(k)),
                    ARRAY[]::text[]
                ) AS cols
            FROM config.source_mappings sm
        )
        UPDATE config.source_mappings sm
        SET template_type = CASE
            WHEN t.cols && {change_sig} THEN 'inventory_change'
            WHEN t.cols && {sale_sig}   THEN 'sales'
            ELSE 'sales'
        END
        FROM targets t
        WHERE t.mv = sm.mapping_version_id
          AND sm.template_type IS NULL;
        """  # noqa: S608 — identifiers fixed; signature arrays are code constants
    )

    # 3. Enforce NOT NULL (idempotent: already NOT NULL on a fresh-bootstrap DB).
    _exec("ALTER TABLE config.source_mappings ALTER COLUMN template_type SET NOT NULL;")

    # 4. Surface template_type through the view (appended last).
    _exec(_VIEW_WITH_TEMPLATE_TYPE)

    # 5. Comment (escape single quotes for the SQL string literal — the comment
    #    text carries an apostrophe, matching the manifest's ''-escaped form).
    comment = _COMMENT.replace("'", "''")
    _exec(f"COMMENT ON COLUMN config.source_mappings.template_type IS '{comment}'")


def downgrade() -> None:
    _guard_target()
    # DROP + recreate the view without template_type (CREATE OR REPLACE cannot drop
    # a column), then drop the column.
    _exec("DROP VIEW IF EXISTS config.source_mappings_v;")
    _exec(_VIEW_PRE_0010)
    _exec("ALTER TABLE config.source_mappings DROP COLUMN IF EXISTS template_type;")
