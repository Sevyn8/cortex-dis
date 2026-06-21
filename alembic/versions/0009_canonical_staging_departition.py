"""canonical + staging event/signal parents: de-partition — D77's scope clause revised

Slice 30a de-partitioned audit.events (migration 0007, D77) and KEPT the other
6 RANGE-partitioned parents — canonical {store_sku_sale_events,
store_sku_change_events, store_sku_signal_history} and the staging mirrors —
because they fail LOUD (batch nack) on a missing partition, so they carried no
silent-loss risk (D77 "Scope", test-pinned by
test_scope_boundary_no_other_parent_departitioned).

This migration consciously REVISES that scope clause (operator-confirmed; see
the register entry cross-referenced from D77): all 6 parents become PLAIN
tables for beta. Same shape as the partitioned form they replace: a fixed
bootstrap-created daily window (0001), no DEFAULT partition, no automation —
past the window every consumer batch nacks. Removal, not automation, is the
beta posture, exactly as 0007.

- upgrade(): per parent, drop the partitioned table (children drop with the
  parent; rows are disposable in BOTH local and Cloud SQL, operator-confirmed,
  no preservation; nothing FK-references any of the six — live-introspected)
  and re-apply its schemas/postgres DDL file verbatim (the manifest pattern,
  as 0001/0007). The DDL files are the source of truth and now declare the
  plain shape: PK (id) — the composites (id, event_date) / (id, as_of_date)
  existed only to satisfy the partition-key-in-PK requirement (the D77 PK
  precedent) — with every column, CHECK (including the event_date/as_of_date
  derivation CHECKs, which define those columns' semantics and are Slice 21's
  re-partition invariants), the signal_history natural keys (which keep
  as_of_date: the daily-snapshot grain, not a partition artifact), FKs,
  secondary indexes, triggers, and FORCE RLS policies unchanged.
- downgrade(): recreate the partitioned forms (frozen pre-0009 shapes, inlined
  below from the pre-edit DDL files at git HEAD — every structural statement
  verbatim, catalog COMMENTs omitted as 0007's frozen block — cross-checked
  against the live catalog on 5433) with FRESH 7-day partition windows
  (CURRENT_DATE-1 .. +5, 0001's logic) — NOT the original 2026-06 dates.
  Rows are not preserved in either direction.

Fresh == migrated by construction: a fresh bootstrap applies the same plain
DDL files at 0001 (whose PARTITIONED list is now empty), then this migration
re-applies them; a migrated DB gets them applied here.

Partitioning, with automation, returns at Slice 21 (BQ archive + eviction,
decisions.md D29/D34) for all 7 tables — these six AND audit.events. The
streaming consumer needs no change (its INSERTs target the parent tables;
partitioning was transparent, and its loud-error/rollback/nack posture for
the CHECK/infra failure class is unchanged).

See: decisions.md D77 (revised), D29/D34 (the buffer + archive that
re-partition at Slice 21), D30/D31/D33 (write/compute/dedup semantics,
unchanged), hard rule 7 (event tables stay UNIQUE-free).

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-07

"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


# This file: alembic/versions/0009_canonical_staging_departition.py -> repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMAS_DIR = _REPO_ROOT / "schemas" / "postgres"

APP_ROLE = "ithina_dis_user"

# Downgrade partition window: CURRENT_DATE - 1 .. CURRENT_DATE + 5 (7 daily), as 0001/0007.
_DAYS_BACK = 1
_DAYS_FORWARD = 5

# The six de-partitioned parents, in apply order (canonical first, then the
# staging mirrors). FKs point only at identity_mirror/config, never at each
# other, so per-parent drop-and-recreate needs no inter-parent ordering.
_PARENTS: tuple[tuple[str, str], ...] = (
    ("canonical", "store_sku_sale_events"),
    ("canonical", "store_sku_change_events"),
    ("canonical", "store_sku_signal_history"),
    ("staging", "store_sku_sale_events"),
    ("staging", "store_sku_change_events"),
    ("staging", "store_sku_signal_history"),
)

# Target-safety guard (0001 SA.2 / 0007 pattern). Expected DIS database name,
# with the Customer Master database hard-blocked regardless.
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"


def _exec(sql: str) -> None:
    """Execute raw SQL via the DBAPI, bypassing SQLAlchemy text() bind parsing.

    The DDL files contain ':' / '::' sequences (e.g. ``::uuid`` casts in the
    RLS policies) that text() would misread as bind parameters. exec_driver_sql
    sends the string straight to psycopg, which also supports the multi-
    statement DDL files.
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

    for schema, table in _PARENTS:
        # 1. Drop the partitioned parent; its partitions drop with it. Nothing
        #    FK-references any of the six (live-introspected). Rows disposable.
        _exec(f'DROP TABLE IF EXISTS "{schema}"."{table}"')

        # 2. Re-apply the DDL file verbatim (manifest pattern, as 0001/0007):
        #    the plain table, constraints, indexes, trigger, FORCE RLS +
        #    policy, comments.
        _exec((_SCHEMAS_DIR / schema / f"{table}.sql").read_text())

        # 3. Backstop grant (0001 step-5 / 0007 step-3 precedent). The schema
        #    default ACLs already cover tables created by the migrating role;
        #    explicit anyway.
        _exec(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{schema}"."{table}" TO {APP_ROLE}')


# The frozen pre-0009 partitioned shapes, for downgrade only. Captured from
# the pre-edit DDL files at git HEAD (comment-only lines and catalog COMMENT
# statements stripped — the 0007 frozen-block posture: shape only; every
# structural statement kept verbatim) and cross-checked against the live
# pre-0009 tables introspected on 5433.
_PARTITIONED_DDL_CANONICAL_SALE_EVENTS = """
CREATE TABLE canonical.store_sku_sale_events (

    id                          UUID                                NOT NULL DEFAULT uuidv7(),

    event_date                  DATE                                NOT NULL,

    tenant_id                   UUID                                NOT NULL,
    store_id                    UUID                                NOT NULL,

    sku_id                      VARCHAR(128) COLLATE "C"            NOT NULL,
    sku_variant                 VARCHAR(128) COLLATE "C"            NULL,
    sku_lot_batch               VARCHAR(128) COLLATE "C"            NULL,

    event_subtype               VARCHAR(32) COLLATE "C"             NOT NULL,

    source_sale_timestamp       TIMESTAMPTZ                         NOT NULL,

    transaction_id              VARCHAR(128) COLLATE "C"            NULL,
    line_item_seq               SMALLINT                            NULL,

    quantity                    NUMERIC(14, 3)                      NOT NULL,
    unit_retail_price           NUMERIC(12, 4)                      NOT NULL,
    unit_sale_price             NUMERIC(12, 4)                      NOT NULL,
    discount_amount             NUMERIC(12, 4)                      NULL,
    discount_pct                NUMERIC(5, 2)                       NULL,
    unit_cost                   NUMERIC(12, 4)                      NULL,
    promo_identifier            VARCHAR(128) COLLATE "C"            NULL,
    tax_amount                  NUMERIC(12, 4)                      NULL,
    tax_treatment               canonical.tax_treatment_enum        NOT NULL,
    currency                    CHAR(3)                             NOT NULL,

    payment_method              VARCHAR(64) COLLATE "C"             NULL,
    customer_token              VARCHAR(128) COLLATE "C"            NULL,

    sale_channel                VARCHAR(32) COLLATE "C"             NULL,

    store_sku_current_position_id  UUID                             NULL,

    related_sale_event_id       UUID                                NULL,

    source_id                   VARCHAR(128) COLLATE "C"            NOT NULL,
    source_event_id             VARCHAR(256) COLLATE "C"            NOT NULL,

    mapping_version_id          BIGINT                              NOT NULL,
    trace_id                    UUID                                NOT NULL,
    dis_channel                 VARCHAR(32) COLLATE "C"             NOT NULL,
    last_updated_at             TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    ingest_metadata             JSONB                               NULL,

    CONSTRAINT pk_ssse
        PRIMARY KEY (id, event_date),

    CONSTRAINT fk_ssse_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_ssse_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT fk_ssse_mapping_version
        FOREIGN KEY (mapping_version_id)
        REFERENCES config.source_mappings (mapping_version_id),

    CONSTRAINT ck_ssse_event_subtype_vocab
        CHECK (event_subtype IN ('SALE', 'RETURN', 'VOID')),

    CONSTRAINT ck_ssse_unit_retail_price_non_negative
        CHECK (unit_retail_price >= 0),

    CONSTRAINT ck_ssse_unit_sale_price_non_negative
        CHECK (unit_sale_price >= 0),

    CONSTRAINT ck_ssse_unit_sale_price_le_retail
        CHECK (unit_sale_price <= unit_retail_price),

    CONSTRAINT ck_ssse_unit_cost_non_negative
        CHECK (unit_cost IS NULL OR unit_cost >= 0),

    CONSTRAINT ck_ssse_discount_amount_non_negative
        CHECK (discount_amount IS NULL OR discount_amount >= 0),

    CONSTRAINT ck_ssse_discount_pct_range
        CHECK (discount_pct IS NULL
            OR (discount_pct >= 0 AND discount_pct <= 100)),

    CONSTRAINT ck_ssse_tax_amount_non_negative
        CHECK (tax_amount IS NULL OR tax_amount >= 0),

    CONSTRAINT ck_ssse_line_item_seq_positive
        CHECK (line_item_seq IS NULL OR line_item_seq > 0),

    CONSTRAINT ck_ssse_return_void_quantity_sign
        CHECK (
            (event_subtype = 'SALE' AND quantity > 0)
            OR (event_subtype IN ('RETURN', 'VOID') AND quantity < 0)
        ),

    CONSTRAINT ck_ssse_event_date_matches_sale_timestamp
        CHECK (event_date = (source_sale_timestamp AT TIME ZONE 'UTC')::date)

) PARTITION BY RANGE (event_date);

CREATE INDEX ix_ssse_tenant_store_sku_time
    ON canonical.store_sku_sale_events
    (tenant_id, store_id, sku_id, source_sale_timestamp);

CREATE INDEX ix_ssse_tenant_store_time
    ON canonical.store_sku_sale_events
    (tenant_id, store_id, source_sale_timestamp);

CREATE INDEX ix_ssse_source_sale_timestamp
    ON canonical.store_sku_sale_events
    (source_sale_timestamp);

CREATE INDEX ix_ssse_transaction_id
    ON canonical.store_sku_sale_events (transaction_id)
    WHERE transaction_id IS NOT NULL;

CREATE INDEX ix_ssse_dedup_key
    ON canonical.store_sku_sale_events
    (tenant_id, store_id, source_id, source_event_id, source_sale_timestamp DESC);

CREATE INDEX ix_ssse_trace_id
    ON canonical.store_sku_sale_events (trace_id);

CREATE INDEX ix_ssse_mapping_version
    ON canonical.store_sku_sale_events (mapping_version_id);

CREATE INDEX ix_ssse_related_sale_event_id
    ON canonical.store_sku_sale_events (related_sale_event_id)
    WHERE related_sale_event_id IS NOT NULL;

CREATE INDEX ix_ssse_current_position
    ON canonical.store_sku_sale_events (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;

CREATE OR REPLACE FUNCTION canonical.set_ssse_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ssse_set_last_updated_at
    BEFORE UPDATE ON canonical.store_sku_sale_events
    FOR EACH ROW
    EXECUTE FUNCTION canonical.set_ssse_last_updated_at();

ALTER TABLE canonical.store_sku_sale_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical.store_sku_sale_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON canonical.store_sku_sale_events
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
"""

_PARTITIONED_DDL_CANONICAL_CHANGE_EVENTS = """
CREATE TABLE canonical.store_sku_change_events (

    id                              UUID                            NOT NULL DEFAULT uuidv7(),

    event_date                      DATE                            NOT NULL,

    tenant_id                       UUID                            NOT NULL,
    store_id                        UUID                            NOT NULL,

    sku_id                          VARCHAR(128) COLLATE "C"        NOT NULL,
    sku_variant                     VARCHAR(128) COLLATE "C"        NULL,
    sku_lot_batch                   VARCHAR(128) COLLATE "C"        NULL,

    store_sku_current_position_id   UUID                            NULL,

    event_category                  VARCHAR(32) COLLATE "C"         NOT NULL,
    event_subtype                   VARCHAR(64) COLLATE "C"         NOT NULL,

    source_event_timestamp          TIMESTAMPTZ                     NOT NULL,
    effective_from                  TIMESTAMPTZ                     NULL,
    effective_until                 TIMESTAMPTZ                     NULL,

    attribute_name                  VARCHAR(64) COLLATE "C"         NULL,
    value_before                    JSONB                           NULL,
    value_after                     JSONB                           NULL,

    numeric_value_before            NUMERIC(14, 4)                  NULL,
    numeric_value_after             NUMERIC(14, 4)                  NULL,
    numeric_change                  NUMERIC(14, 4)                  NULL,

    reason_code                     VARCHAR(64) COLLATE "C"         NULL,
    reason_note                     VARCHAR(256)                    NULL,

    change_context                  JSONB                           NULL,

    source_id                       VARCHAR(128) COLLATE "C"        NOT NULL,
    source_event_id                 VARCHAR(256) COLLATE "C"        NOT NULL,

    mapping_version_id              BIGINT                          NOT NULL,
    trace_id                        UUID                            NOT NULL,
    dis_channel                     VARCHAR(32) COLLATE "C"         NOT NULL,
    last_updated_at                 TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),

    ingest_metadata                 JSONB                           NULL,

    CONSTRAINT pk_ssce
        PRIMARY KEY (id, event_date),

    CONSTRAINT fk_ssce_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_ssce_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT fk_ssce_mapping_version
        FOREIGN KEY (mapping_version_id)
        REFERENCES config.source_mappings (mapping_version_id),

    CONSTRAINT ck_ssce_event_category_vocab
        CHECK (event_category IN (
            'INVENTORY', 'PRICE', 'COST', 'REGULATORY',
            'STATUS', 'CATALOGUE', 'OTHER'
        )),

    CONSTRAINT ck_ssce_event_date_matches_source_ts
        CHECK (event_date = (source_event_timestamp AT TIME ZONE 'UTC')::date),

    CONSTRAINT ck_ssce_effective_until_after_from
        CHECK (
            effective_until IS NULL
            OR effective_from IS NULL
            OR effective_until > effective_from
        ),

    CONSTRAINT ck_ssce_at_least_one_value_present
        CHECK (value_before IS NOT NULL OR value_after IS NOT NULL)

) PARTITION BY RANGE (event_date);

CREATE INDEX ix_ssce_tenant_store_sku_category_time
    ON canonical.store_sku_change_events
    (tenant_id, store_id, sku_id, sku_variant, sku_lot_batch,
     event_category, source_event_timestamp DESC);

CREATE INDEX ix_ssce_tenant_store_category_time
    ON canonical.store_sku_change_events
    (tenant_id, store_id, event_category, source_event_timestamp);

CREATE INDEX ix_ssce_source_event_timestamp
    ON canonical.store_sku_change_events (source_event_timestamp);

CREATE INDEX ix_ssce_dedup_key
    ON canonical.store_sku_change_events
    (tenant_id, store_id, source_id, source_event_id, source_event_timestamp DESC);

CREATE INDEX ix_ssce_current_position
    ON canonical.store_sku_change_events (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;

CREATE INDEX ix_ssce_trace_id
    ON canonical.store_sku_change_events (trace_id);

CREATE INDEX ix_ssce_mapping_version
    ON canonical.store_sku_change_events (mapping_version_id);

CREATE OR REPLACE FUNCTION canonical.set_ssce_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ssce_set_last_updated_at
    BEFORE UPDATE ON canonical.store_sku_change_events
    FOR EACH ROW
    EXECUTE FUNCTION canonical.set_ssce_last_updated_at();

ALTER TABLE canonical.store_sku_change_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical.store_sku_change_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON canonical.store_sku_change_events
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
"""

_PARTITIONED_DDL_CANONICAL_SIGNAL_HISTORY = """
CREATE TABLE canonical.store_sku_signal_history (

    id                              UUID                            NOT NULL DEFAULT uuidv7(),

    as_of_date                      DATE                            NOT NULL,

    tenant_id                       UUID                            NOT NULL,
    store_id                        UUID                            NOT NULL,

    sku_id                          VARCHAR(128) COLLATE "C"        NOT NULL,
    sku_variant                     VARCHAR(128) COLLATE "C"        NULL,
    sku_lot_batch                   VARCHAR(128) COLLATE "C"        NULL,

    store_sku_current_position_id   UUID                            NULL,

    velocity_7day                   NUMERIC(10, 4)                  NULL,
    stock_age_days                  SMALLINT                        NULL,
    unit_cost_trend_30day           NUMERIC(12, 4)                  NULL,

    trace_id                        UUID                            NOT NULL,
    created_at                      TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    compute_metadata                JSONB                           NULL,

    CONSTRAINT pk_sssh
        PRIMARY KEY (id, as_of_date),

    CONSTRAINT uq_sssh_natural
        UNIQUE NULLS NOT DISTINCT
        (tenant_id, store_id, sku_id, sku_variant, sku_lot_batch, as_of_date),

    CONSTRAINT fk_sssh_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_sssh_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT ck_sssh_velocity_7day_non_negative
        CHECK (velocity_7day IS NULL OR velocity_7day >= 0),

    CONSTRAINT ck_sssh_stock_age_days_non_negative
        CHECK (stock_age_days IS NULL OR stock_age_days >= 0),

    CONSTRAINT ck_sssh_as_of_date_not_future
        CHECK (as_of_date <= CURRENT_DATE)

) PARTITION BY RANGE (as_of_date);

CREATE INDEX ix_sssh_tenant_store_as_of
    ON canonical.store_sku_signal_history (tenant_id, store_id, as_of_date);

CREATE INDEX ix_sssh_as_of_date
    ON canonical.store_sku_signal_history (as_of_date);

CREATE INDEX ix_sssh_current_position
    ON canonical.store_sku_signal_history (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;

CREATE INDEX ix_sssh_trace_id
    ON canonical.store_sku_signal_history (trace_id);

ALTER TABLE canonical.store_sku_signal_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical.store_sku_signal_history FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON canonical.store_sku_signal_history
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
"""

_PARTITIONED_DDL_STAGING_SALE_EVENTS = """
CREATE TABLE staging.store_sku_sale_events (

    id                          UUID                                NOT NULL DEFAULT uuidv7(),

    event_date                  DATE                                NOT NULL,

    tenant_id                   UUID                                NOT NULL,
    store_id                    UUID                                NOT NULL,

    sku_id                      VARCHAR(128) COLLATE "C"            NOT NULL,
    sku_variant                 VARCHAR(128) COLLATE "C"            NULL,
    sku_lot_batch               VARCHAR(128) COLLATE "C"            NULL,

    event_subtype               VARCHAR(32) COLLATE "C"             NOT NULL,

    source_sale_timestamp       TIMESTAMPTZ                         NOT NULL,

    transaction_id              VARCHAR(128) COLLATE "C"            NULL,
    line_item_seq               SMALLINT                            NULL,

    quantity                    NUMERIC(14, 3)                      NOT NULL,
    unit_retail_price           NUMERIC(12, 4)                      NOT NULL,
    unit_sale_price             NUMERIC(12, 4)                      NOT NULL,
    discount_amount             NUMERIC(12, 4)                      NULL,
    discount_pct                NUMERIC(5, 2)                       NULL,
    unit_cost                   NUMERIC(12, 4)                      NULL,
    promo_identifier            VARCHAR(128) COLLATE "C"            NULL,
    tax_amount                  NUMERIC(12, 4)                      NULL,
    tax_treatment               staging.tax_treatment_enum        NOT NULL,
    currency                    CHAR(3)                             NOT NULL,

    payment_method              VARCHAR(64) COLLATE "C"             NULL,
    customer_token              VARCHAR(128) COLLATE "C"            NULL,

    sale_channel                VARCHAR(32) COLLATE "C"             NULL,

    store_sku_current_position_id  UUID                             NULL,

    related_sale_event_id       UUID                                NULL,

    mapping_version_id          BIGINT                              NOT NULL,
    trace_id                    UUID                                NOT NULL,
    dis_channel                 VARCHAR(32) COLLATE "C"             NOT NULL,
    last_updated_at             TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    ingest_metadata             JSONB                               NULL,

    CONSTRAINT pk_st_ssse
        PRIMARY KEY (id, event_date),

    CONSTRAINT fk_st_ssse_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_st_ssse_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT fk_st_ssse_mapping_version
        FOREIGN KEY (mapping_version_id)
        REFERENCES config.source_mappings (mapping_version_id),

    CONSTRAINT ck_st_ssse_event_subtype_vocab
        CHECK (event_subtype IN ('SALE', 'RETURN', 'VOID')),

    CONSTRAINT ck_st_ssse_unit_retail_price_non_negative
        CHECK (unit_retail_price >= 0),

    CONSTRAINT ck_st_ssse_unit_sale_price_non_negative
        CHECK (unit_sale_price >= 0),

    CONSTRAINT ck_st_ssse_unit_sale_price_le_retail
        CHECK (unit_sale_price <= unit_retail_price),

    CONSTRAINT ck_st_ssse_unit_cost_non_negative
        CHECK (unit_cost IS NULL OR unit_cost >= 0),

    CONSTRAINT ck_st_ssse_discount_amount_non_negative
        CHECK (discount_amount IS NULL OR discount_amount >= 0),

    CONSTRAINT ck_st_ssse_discount_pct_range
        CHECK (discount_pct IS NULL
            OR (discount_pct >= 0 AND discount_pct <= 100)),

    CONSTRAINT ck_st_ssse_tax_amount_non_negative
        CHECK (tax_amount IS NULL OR tax_amount >= 0),

    CONSTRAINT ck_st_ssse_line_item_seq_positive
        CHECK (line_item_seq IS NULL OR line_item_seq > 0),

    CONSTRAINT ck_st_ssse_return_void_quantity_sign
        CHECK (
            (event_subtype = 'SALE' AND quantity > 0)
            OR (event_subtype IN ('RETURN', 'VOID') AND quantity < 0)
        ),

    CONSTRAINT ck_st_ssse_event_date_matches_sale_timestamp
        CHECK (event_date = (source_sale_timestamp AT TIME ZONE 'UTC')::date)

) PARTITION BY RANGE (event_date);

CREATE INDEX ix_st_ssse_tenant_store_sku_time
    ON staging.store_sku_sale_events
    (tenant_id, store_id, sku_id, source_sale_timestamp);

CREATE INDEX ix_st_ssse_tenant_store_time
    ON staging.store_sku_sale_events
    (tenant_id, store_id, source_sale_timestamp);

CREATE INDEX ix_st_ssse_source_sale_timestamp
    ON staging.store_sku_sale_events
    (source_sale_timestamp);

CREATE INDEX ix_st_ssse_transaction_id
    ON staging.store_sku_sale_events (transaction_id)
    WHERE transaction_id IS NOT NULL;

CREATE INDEX ix_st_ssse_trace_id
    ON staging.store_sku_sale_events (trace_id);

CREATE INDEX ix_st_ssse_mapping_version
    ON staging.store_sku_sale_events (mapping_version_id);

CREATE INDEX ix_st_ssse_related_sale_event_id
    ON staging.store_sku_sale_events (related_sale_event_id)
    WHERE related_sale_event_id IS NOT NULL;

CREATE INDEX ix_st_ssse_current_position
    ON staging.store_sku_sale_events (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;

CREATE OR REPLACE FUNCTION staging.set_st_ssse_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_st_ssse_set_last_updated_at
    BEFORE UPDATE ON staging.store_sku_sale_events
    FOR EACH ROW
    EXECUTE FUNCTION staging.set_st_ssse_last_updated_at();

ALTER TABLE staging.store_sku_sale_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.store_sku_sale_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON staging.store_sku_sale_events
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
"""

_PARTITIONED_DDL_STAGING_CHANGE_EVENTS = """
CREATE TABLE staging.store_sku_change_events (

    id                              UUID                            NOT NULL DEFAULT uuidv7(),

    event_date                      DATE                            NOT NULL,

    tenant_id                       UUID                            NOT NULL,
    store_id                        UUID                            NOT NULL,

    sku_id                          VARCHAR(128) COLLATE "C"        NOT NULL,
    sku_variant                     VARCHAR(128) COLLATE "C"        NULL,
    sku_lot_batch                   VARCHAR(128) COLLATE "C"        NULL,

    store_sku_current_position_id   UUID                            NULL,

    event_category                  VARCHAR(32) COLLATE "C"         NOT NULL,
    event_subtype                   VARCHAR(64) COLLATE "C"         NOT NULL,

    source_event_timestamp          TIMESTAMPTZ                     NOT NULL,
    effective_from                  TIMESTAMPTZ                     NULL,
    effective_until                 TIMESTAMPTZ                     NULL,

    attribute_name                  VARCHAR(64) COLLATE "C"         NULL,
    value_before                    JSONB                           NULL,
    value_after                     JSONB                           NULL,

    numeric_value_before            NUMERIC(14, 4)                  NULL,
    numeric_value_after             NUMERIC(14, 4)                  NULL,
    numeric_change                  NUMERIC(14, 4)                  NULL,

    reason_code                     VARCHAR(64) COLLATE "C"         NULL,
    reason_note                     VARCHAR(256)                    NULL,

    change_context                  JSONB                           NULL,

    mapping_version_id              BIGINT                          NOT NULL,
    trace_id                        UUID                            NOT NULL,
    dis_channel                     VARCHAR(32) COLLATE "C"         NOT NULL,
    last_updated_at                 TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),

    ingest_metadata                 JSONB                           NULL,

    CONSTRAINT pk_st_ssce
        PRIMARY KEY (id, event_date),

    CONSTRAINT fk_st_ssce_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_st_ssce_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT fk_st_ssce_mapping_version
        FOREIGN KEY (mapping_version_id)
        REFERENCES config.source_mappings (mapping_version_id),

    CONSTRAINT ck_st_ssce_event_category_vocab
        CHECK (event_category IN (
            'INVENTORY', 'PRICE', 'COST', 'REGULATORY',
            'STATUS', 'CATALOGUE', 'OTHER'
        )),

    CONSTRAINT ck_st_ssce_event_date_matches_source_ts
        CHECK (event_date = (source_event_timestamp AT TIME ZONE 'UTC')::date),

    CONSTRAINT ck_st_ssce_effective_until_after_from
        CHECK (
            effective_until IS NULL
            OR effective_from IS NULL
            OR effective_until > effective_from
        ),

    CONSTRAINT ck_st_ssce_at_least_one_value_present
        CHECK (value_before IS NOT NULL OR value_after IS NOT NULL)

) PARTITION BY RANGE (event_date);

CREATE INDEX ix_st_ssce_tenant_store_sku_category_time
    ON staging.store_sku_change_events
    (tenant_id, store_id, sku_id, sku_variant, sku_lot_batch,
     event_category, source_event_timestamp DESC);

CREATE INDEX ix_st_ssce_tenant_store_category_time
    ON staging.store_sku_change_events
    (tenant_id, store_id, event_category, source_event_timestamp);

CREATE INDEX ix_st_ssce_source_event_timestamp
    ON staging.store_sku_change_events (source_event_timestamp);

CREATE INDEX ix_st_ssce_current_position
    ON staging.store_sku_change_events (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;

CREATE INDEX ix_st_ssce_trace_id
    ON staging.store_sku_change_events (trace_id);

CREATE INDEX ix_st_ssce_mapping_version
    ON staging.store_sku_change_events (mapping_version_id);

CREATE OR REPLACE FUNCTION staging.set_st_ssce_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_st_ssce_set_last_updated_at
    BEFORE UPDATE ON staging.store_sku_change_events
    FOR EACH ROW
    EXECUTE FUNCTION staging.set_st_ssce_last_updated_at();

ALTER TABLE staging.store_sku_change_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.store_sku_change_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON staging.store_sku_change_events
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
"""

_PARTITIONED_DDL_STAGING_SIGNAL_HISTORY = """
CREATE TABLE staging.store_sku_signal_history (

    id                              UUID                            NOT NULL DEFAULT uuidv7(),

    as_of_date                      DATE                            NOT NULL,

    tenant_id                       UUID                            NOT NULL,
    store_id                        UUID                            NOT NULL,

    sku_id                          VARCHAR(128) COLLATE "C"        NOT NULL,
    sku_variant                     VARCHAR(128) COLLATE "C"        NULL,
    sku_lot_batch                   VARCHAR(128) COLLATE "C"        NULL,

    store_sku_current_position_id   UUID                            NULL,

    velocity_7day                   NUMERIC(10, 4)                  NULL,
    stock_age_days                  SMALLINT                        NULL,
    unit_cost_trend_30day           NUMERIC(12, 4)                  NULL,

    trace_id                        UUID                            NOT NULL,
    created_at                      TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    compute_metadata                JSONB                           NULL,

    CONSTRAINT pk_st_sssh
        PRIMARY KEY (id, as_of_date),

    CONSTRAINT uq_st_sssh_natural
        UNIQUE NULLS NOT DISTINCT
        (tenant_id, store_id, sku_id, sku_variant, sku_lot_batch, as_of_date),

    CONSTRAINT fk_st_sssh_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES identity_mirror.tenants (tenant_id),

    CONSTRAINT fk_st_sssh_store
        FOREIGN KEY (tenant_id, store_id)
        REFERENCES identity_mirror.stores (tenant_id, store_id),

    CONSTRAINT ck_st_sssh_velocity_7day_non_negative
        CHECK (velocity_7day IS NULL OR velocity_7day >= 0),

    CONSTRAINT ck_st_sssh_stock_age_days_non_negative
        CHECK (stock_age_days IS NULL OR stock_age_days >= 0),

    CONSTRAINT ck_st_sssh_as_of_date_not_future
        CHECK (as_of_date <= CURRENT_DATE)

) PARTITION BY RANGE (as_of_date);

CREATE INDEX ix_st_sssh_tenant_store_as_of
    ON staging.store_sku_signal_history (tenant_id, store_id, as_of_date);

CREATE INDEX ix_st_sssh_as_of_date
    ON staging.store_sku_signal_history (as_of_date);

CREATE INDEX ix_st_sssh_current_position
    ON staging.store_sku_signal_history (store_sku_current_position_id)
    WHERE store_sku_current_position_id IS NOT NULL;

CREATE INDEX ix_st_sssh_trace_id
    ON staging.store_sku_signal_history (trace_id);

ALTER TABLE staging.store_sku_signal_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.store_sku_signal_history FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation
    ON staging.store_sku_signal_history
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
"""

# Frozen DDL per parent, downgrade-only. Keyed by (schema, table), in
# _PARENTS order.
_PARTITIONED_DDL: dict[tuple[str, str], str] = {
    ("canonical", "store_sku_sale_events"): _PARTITIONED_DDL_CANONICAL_SALE_EVENTS,
    ("canonical", "store_sku_change_events"): _PARTITIONED_DDL_CANONICAL_CHANGE_EVENTS,
    ("canonical", "store_sku_signal_history"): _PARTITIONED_DDL_CANONICAL_SIGNAL_HISTORY,
    ("staging", "store_sku_sale_events"): _PARTITIONED_DDL_STAGING_SALE_EVENTS,
    ("staging", "store_sku_change_events"): _PARTITIONED_DDL_STAGING_CHANGE_EVENTS,
    ("staging", "store_sku_signal_history"): _PARTITIONED_DDL_STAGING_SIGNAL_HISTORY,
}


def downgrade() -> None:
    # Same target-safety guard before any destructive DDL.
    _guard_target()

    # Recreate the partitioned forms with FRESH windows (CURRENT_DATE-relative,
    # 0001's logic) -- not the original 2026-06 dates. No row preservation.
    for schema, table in _PARENTS:
        _exec(f'DROP TABLE IF EXISTS "{schema}"."{table}"')
        _exec(_PARTITIONED_DDL[(schema, table)])

    start = cast(date, op.get_bind().exec_driver_sql("SELECT CURRENT_DATE").scalar())
    start = start - timedelta(days=_DAYS_BACK)
    span = _DAYS_BACK + _DAYS_FORWARD + 1  # inclusive -> 7 days
    for schema, table in _PARENTS:
        for i in range(span):
            day = start + timedelta(days=i)
            nxt = day + timedelta(days=1)
            pname = f"{table}_p{day.strftime('%Y%m%d')}"
            _exec(
                f'CREATE TABLE IF NOT EXISTS "{schema}"."{pname}" '
                f'PARTITION OF "{schema}"."{table}" '
                f"FOR VALUES FROM ('{day.isoformat()}') TO ('{nxt.isoformat()}')"
            )

    _exec(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA canonical TO {APP_ROLE}")
    _exec(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA staging TO {APP_ROLE}")
