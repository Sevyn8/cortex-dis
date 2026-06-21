"""two-GUC PLATFORM/TENANT RLS: asymmetric policy rewrite on all 13 tenant-scoped tables — Slice 17b

Realizes D76. Each tenant-scoped policy gains a PLATFORM read branch in USING
(see-all) while WITH CHECK stays tenant-pinned. The asymmetry is the whole design:
PLATFORM widens reads but NEVER writes — a PLATFORM-no-tenant session writes nothing
(the tenant GUC is empty, NULLIF -> NULL, matches no row in WITH CHECK), a
PLATFORM-with-tenant session writes only that tenant (impersonation). This is the
deliberate divergence from Customer Master, which put the PLATFORM branch in WITH CHECK
too and so allowed cross-tenant writes.

The tenant comparison is NULLIF-wrapped (``NULLIF(current_setting('app.tenant_id',
true), '')::uuid``) so a PLATFORM-no-tenant session (empty/unset tenant GUC) matches no
rows instead of erroring on the ``::uuid`` cast. user_type is read fail-closed:
``current_setting('app.user_type', true)`` is NULL when unset and never equals
'PLATFORM'.

- upgrade(): DROP POLICY IF EXISTS + CREATE POLICY (inline) for all 13, to the EXACT
  end-state now in the schemas/postgres/*.sql DDL files (the 0001 manifest applies the
  same text on a fresh bootstrap, so fresh == migrated at head). 12 ``tenant_isolation``
  policies (USING: NULLIF tenant-match OR user_type=PLATFORM; WITH CHECK: NULLIF
  tenant-match only) + ``audit.events`` ``rls_audit_events_tenant`` (USING-only: NULLIF
  tenant-match OR tenant_id IS NULL OR user_type=PLATFORM; no WITH CHECK).
- downgrade(): DROP POLICY IF EXISTS + CREATE POLICY restoring the EXACT pre-slice
  single-GUC form (re-grepped live from pg_policies before authoring): the 12 with
  USING = WITH CHECK = ``tenant_id = current_setting('app.tenant_id', true)::uuid`` (no
  NULLIF, no PLATFORM branch); ``audit.events`` USING-only = ``tenant_id =
  current_setting('app.tenant_id', true)::uuid OR tenant_id IS NULL`` (no WITH CHECK).

Inline SQL only (never re-reads the DDL files at runtime); never ALTER POLICY (DROP+
CREATE matches the 0005 precedent and is the only policy-rewrite idiom in this repo);
never edits shipped migrations 0005/0007/0009. ENABLE/FORCE ROW LEVEL SECURITY is left
untouched (already on for all 13 since 0001/0005) — this migration only swaps the policy
predicate.

See: docs/slices/slice-17b-two-guc-platform-rls.md, decisions.md D76 (realized), D41
(identity_mirror RLS-OFF, untouched), D69 (config RLS), the 0007/0009 fresh==migrated
precedent.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-15

"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# Target-safety guard (Slice 1 §A.2 pattern, copied from 0001..0005).
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"

# The 12 tables carrying the symmetric ``tenant_isolation`` policy.
_TENANT_ISOLATION_TABLES = (
    "bronze.data_ingress_events",
    "canonical.store_sku_change_events",
    "canonical.store_sku_current_position",
    "canonical.store_sku_sale_events",
    "canonical.store_sku_signal_history",
    "config.source_mappings",
    "quarantine.quarantined_chunks",
    "quarantine.quarantined_rows",
    "staging.store_sku_change_events",
    "staging.store_sku_current_position",
    "staging.store_sku_sale_events",
    "staging.store_sku_signal_history",
)
_TENANT_POLICY = "tenant_isolation"

# The audit.events outlier: USING-only, no WITH CHECK, keeps the tenant-less branch.
_AUDIT_TABLE = "audit.events"
_AUDIT_POLICY = "rls_audit_events_tenant"


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
    """Raw DBAPI execution, bypassing text() bind parsing (the ``::`` casts in the
    RLS policy would otherwise misparse as bind parameters)."""
    op.get_bind().exec_driver_sql(sql)


# ---- Policy SQL (inline literals; never read from the DDL files at runtime) ----


def _two_guc_tenant_policy(table: str) -> str:
    """Asymmetric two-GUC form: PLATFORM widens USING only; WITH CHECK stays pinned."""
    return (
        f"CREATE POLICY {_TENANT_POLICY} ON {table} "
        "AS PERMISSIVE FOR ALL TO PUBLIC "
        "USING ("
        "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
        "OR current_setting('app.user_type', true) = 'PLATFORM'"
        ") "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def _single_guc_tenant_policy(table: str) -> str:
    """Exact pre-slice form (re-grepped live): symmetric, no NULLIF, no PLATFORM branch."""
    return (
        f"CREATE POLICY {_TENANT_POLICY} ON {table} "
        "AS PERMISSIVE FOR ALL TO PUBLIC "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


# audit.events two-GUC: USING-only, keeps the tenant-less branch, adds PLATFORM.
_TWO_GUC_AUDIT_POLICY = (
    f"CREATE POLICY {_AUDIT_POLICY} ON {_AUDIT_TABLE} "
    "USING ("
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
    "OR tenant_id IS NULL "
    "OR current_setting('app.user_type', true) = 'PLATFORM'"
    ")"
)

# audit.events exact pre-slice form (USING-only, no NULLIF, no PLATFORM branch).
_SINGLE_GUC_AUDIT_POLICY = (
    f"CREATE POLICY {_AUDIT_POLICY} ON {_AUDIT_TABLE} "
    "USING ("
    "tenant_id = current_setting('app.tenant_id', true)::uuid "
    "OR tenant_id IS NULL"
    ")"
)


def upgrade() -> None:
    _guard_target()
    # 12 symmetric tenant tables -> asymmetric two-GUC. DROP-IF-EXISTS precedes every
    # CREATE so the migration is idempotent over a fresh-bootstrap DB (0001 applied the
    # same end-state from the edited DDL) and a migrated DB (single-GUC at 0010) alike.
    for table in _TENANT_ISOLATION_TABLES:
        _exec(f"DROP POLICY IF EXISTS {_TENANT_POLICY} ON {table}")
        _exec(_two_guc_tenant_policy(table))
    # audit.events outlier: USING-only, no WITH CHECK.
    _exec(f"DROP POLICY IF EXISTS {_AUDIT_POLICY} ON {_AUDIT_TABLE}")
    _exec(_TWO_GUC_AUDIT_POLICY)


def downgrade() -> None:
    _guard_target()
    # Restore the EXACT pre-slice single-GUC form (no NULLIF, no PLATFORM branch).
    for table in _TENANT_ISOLATION_TABLES:
        _exec(f"DROP POLICY IF EXISTS {_TENANT_POLICY} ON {table}")
        _exec(_single_guc_tenant_policy(table))
    _exec(f"DROP POLICY IF EXISTS {_AUDIT_POLICY} ON {_AUDIT_TABLE}")
    _exec(_SINGLE_GUC_AUDIT_POLICY)
