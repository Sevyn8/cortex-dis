"""A Customer-Master-shaped test Postgres for DB-pull tests (Slice 7).

The Slice 2 Customer Master *fake* is HTTP-only (JWTs / sessions / events); it has no
``core`` schema, so it cannot serve the Mirror Sync DB-pull read, which reads CM's Postgres
directly. The real CM (port 5432) is off-limits to tests. This module provisions a faithful
stand-in **inside the DIS 5433 cluster** as a separate database ``ithina_platform_db``:
``core.tenants`` / ``core.stores`` with **FORCE ROW LEVEL SECURITY** and the platform-access
policy, seeded from :mod:`dis_testing.fixtures`, with ``SELECT`` granted to the NOBYPASSRLS
service role so the no-context-→-zero-rows behavior is exercised for real.

Why this is safe for target safety: the reader connects to ``ithina_platform_db`` →
``current_database()`` is the expected CM database (the assertion passes); the writer connects
to ``ithina_dis_db`` → the dis-rls guard passes. Both are on 5433; **the real CM (5432) is never
touched**. This harness is reusable: a later CM-reading slice reuses it rather than rebuilding it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

import dis_testing.fixtures as fx

# The Customer Master database name (the reader's target assertion expects this; local CM and
# the cloud read replica share it). Created here inside the 5433 cluster as the test stand-in.
CM_TEST_DB_NAME = "ithina_platform_db"

# The D55 nullable-store_code edge store. Test-CM-ONLY: it is deliberately NOT in
# fx.STORES (the baseline is all-coded/ACTIVE) — a scoped edge inserted into the
# stand-in by test_db_pull so the sync's code-less path runs end to end, then
# reverted in that test's teardown (HARD REVERT RULE). Parented to buc-ees.
CODELESS_EDGE_STORE_ID = UUID("019e5e3c-0000-7000-8000-0000000000d5")
_CODELESS_EDGE_TS = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)

# The NOBYPASSRLS service role granted SELECT on the test CM, so RLS actually applies to reads.
_READER_ROLE = "ithina_dis_user"

_CORE_DDL = (
    "CREATE SCHEMA IF NOT EXISTS core",
    # display_code / store_code are NULLABLE — matching live CM (introspected,
    # D55 as corrected). A harness stricter than live would mask a real null path.
    """
    CREATE TABLE IF NOT EXISTS core.tenants (
        id            uuid PRIMARY KEY,
        name          text NOT NULL,
        display_code  text,
        status        text NOT NULL,
        created_at    timestamptz NOT NULL,
        updated_at    timestamptz NOT NULL,
        suspended_at  timestamptz,
        terminated_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS core.stores (
        id            uuid PRIMARY KEY,
        tenant_id     uuid NOT NULL REFERENCES core.tenants (id),
        name          text NOT NULL,
        store_code    text,
        status        text NOT NULL,
        country       text NOT NULL,
        timezone      text NOT NULL,
        currency      char(3) NOT NULL,
        tax_treatment text NOT NULL,
        created_at    timestamptz NOT NULL,
        updated_at    timestamptz NOT NULL,
        closed_at     timestamptz
    )
    """,
    # Idempotent upgrade for a test CM provisioned before the code columns existed
    # (CREATE TABLE IF NOT EXISTS does not evolve an existing table).
    "ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS display_code text",
    "ALTER TABLE core.stores ADD COLUMN IF NOT EXISTS store_code text",
    "ALTER TABLE core.tenants ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.tenants FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.stores ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.stores FORCE ROW LEVEL SECURITY",
    # Mirrors the live CM policy shape (introspected): tenant-scoped OR platform sees all.
    "DROP POLICY IF EXISTS tenants_self_access ON core.tenants",
    """
    CREATE POLICY tenants_self_access ON core.tenants
        USING (
            (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            OR current_setting('app.user_type', true) = 'PLATFORM'
        )
    """,
    "DROP POLICY IF EXISTS stores_tenant_isolation ON core.stores",
    """
    CREATE POLICY stores_tenant_isolation ON core.stores
        USING (
            (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            OR current_setting('app.user_type', true) = 'PLATFORM'
        )
    """,
    f"GRANT USAGE ON SCHEMA core TO {_READER_ROLE}",
    f"GRANT SELECT ON core.tenants TO {_READER_ROLE}",
    f"GRANT SELECT ON core.stores TO {_READER_ROLE}",
)

# Seeds converge to fixture truth on conflict (not DO NOTHING): a test CM
# provisioned before the code columns existed must still end up carrying the
# fixture display_code/store_code values after a re-seed.
_SEED_TENANT = text(
    """
    INSERT INTO core.tenants (id, name, display_code, status, created_at, updated_at)
    VALUES (:id, :name, :display_code, :status, :created_at, :updated_at)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        display_code = EXCLUDED.display_code,
        status = EXCLUDED.status,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at
    """
)
_SEED_STORE = text(
    """
    INSERT INTO core.stores
        (id, tenant_id, name, store_code, status, country, timezone, currency, tax_treatment,
         created_at, updated_at)
    VALUES
        (:id, :tenant_id, :name, :store_code, :status, :country, :timezone, :currency, :tax_treatment,
         :created_at, :updated_at)
    ON CONFLICT (id) DO UPDATE SET
        tenant_id = EXCLUDED.tenant_id,
        name = EXCLUDED.name,
        store_code = EXCLUDED.store_code,
        status = EXCLUDED.status,
        country = EXCLUDED.country,
        timezone = EXCLUDED.timezone,
        currency = EXCLUDED.currency,
        tax_treatment = EXCLUDED.tax_treatment,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at
    """
)


def _swap_db(url: str, database: str) -> str:
    return make_url(url).set(database=database).render_as_string(hide_password=False)


def reader_url_from(user_url: str) -> str:
    """Derive the CM read DSN: the DIS service-role URL, pointed at the test CM database."""
    return _swap_db(user_url, CM_TEST_DB_NAME)


def cm_admin_engine(admin_url: str) -> Engine:
    """A sync engine on the test CM database as the admin role (superuser bypasses RLS).

    Tests use this for independent re-reads and for mutating CM rows (converge/idempotence).
    """
    return create_engine(_swap_db(admin_url, CM_TEST_DB_NAME))


def provision_test_cm(admin_url: str) -> None:
    """Create (idempotently) the test CM database, schema, RLS, grants, and seed it.

    ``admin_url`` is the DIS admin DSN (``POSTGRES_ADMIN_URL``); the database name is swapped
    as needed. Safe to call repeatedly.
    """
    # 1. CREATE DATABASE (cannot run in a transaction → AUTOCOMMIT), guarded by existence.
    maintenance = create_engine(_swap_db(admin_url, "postgres"))
    try:
        with maintenance.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": CM_TEST_DB_NAME}
            ).first()
            if exists is None:
                conn.execute(text(f'CREATE DATABASE "{CM_TEST_DB_NAME}"'))
    finally:
        maintenance.dispose()

    # 2. Schema + RLS + grants, then seed (admin is superuser → bypasses RLS to seed).
    engine = cm_admin_engine(admin_url)
    try:
        with engine.begin() as conn:
            for stmt in _CORE_DDL:
                conn.execute(text(stmt))
        seed_test_cm(engine)
    finally:
        engine.dispose()


_INSERT_CODELESS_EDGE_STORE = text(
    """
    INSERT INTO core.stores
        (id, tenant_id, name, store_code, status, country, timezone, currency, tax_treatment,
         created_at, updated_at)
    VALUES
        (:id, :tenant_id, :name, NULL, :status, :country, :timezone, :currency, :tax_treatment,
         :created_at, :updated_at)
    ON CONFLICT (id) DO NOTHING
    """
)


def insert_codeless_edge_store(engine: Engine) -> None:
    """Insert the D55 code-less edge store into the test-CM stand-in. Idempotent.

    Test-CM-only (NOT in fx.STORES): an INACTIVE, store_code=NULL store under
    buc-ees so the sync's nullable-code path runs end to end. Reverted by
    :func:`delete_codeless_edge_store` in the calling test's teardown.
    """
    with engine.begin() as conn:
        conn.execute(
            _INSERT_CODELESS_EDGE_STORE,
            {
                "id": str(CODELESS_EDGE_STORE_ID),
                "tenant_id": str(fx.PRIMARY_TENANT.uuid),
                "name": "Edge Codeless (D55)",
                "status": "INACTIVE",
                "country": "USA",
                "timezone": "America/Chicago",
                "currency": "USD",
                "tax_treatment": "EXCLUSIVE",
                "created_at": _CODELESS_EDGE_TS,
                "updated_at": _CODELESS_EDGE_TS,
            },
        )


def delete_codeless_edge_store(engine: Engine) -> None:
    """Remove the D55 code-less edge store from the test-CM stand-in (teardown)."""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM core.stores WHERE id = :id"),
            {"id": str(CODELESS_EDGE_STORE_ID)},
        )


def seed_test_cm(engine: Engine) -> None:
    """Seed the default fixture tenants/stores into the test CM. Idempotent."""
    with engine.begin() as conn:
        for tenant in fx.TENANTS:
            conn.execute(
                _SEED_TENANT,
                {
                    "id": str(tenant.uuid),
                    "name": tenant.name,
                    "display_code": tenant.display_code,
                    "status": tenant.status,
                    "created_at": tenant.pc_created_at,
                    "updated_at": tenant.pc_updated_at,
                },
            )
        for store in fx.STORES:
            conn.execute(
                _SEED_STORE,
                {
                    "id": str(store.uuid),
                    "tenant_id": str(fx.tenant_uuid_for(store.tenant_display_code)),
                    "name": store.name,
                    "store_code": store.store_code,  # None for the code-less store (D55)
                    "status": store.status,
                    "country": store.country,
                    "timezone": store.timezone,
                    "currency": store.currency,
                    "tax_treatment": store.tax_treatment,
                    "created_at": store.pc_created_at,
                    "updated_at": store.pc_updated_at,
                },
            )
