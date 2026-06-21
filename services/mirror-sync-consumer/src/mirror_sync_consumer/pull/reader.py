"""Read Customer Master's tenants and stores under the platform read context.

This is the only place in DIS that reads Customer Master's DB schema (service CLAUDE.md).
It does **not** use ``dis-rls`` ``rls_session`` — that helper refuses any database that is
not ``ithina_dis_db`` (``dis_rls/session.py``), which is exactly the CM database we must
read here. So the CM read carries its own connection and its own, symmetric, target guard.

Read contract (``docs/ithina_master_db_read_access.md`` §2, §5): ``core.tenants`` /
``core.stores`` are FORCE-RLS; the read must run inside a transaction with
``app.user_type='PLATFORM'`` and ``app.tenant_id=NULL`` set transaction-locally via
``set_config(..., TRUE)``. If the context is unset/mis-set the read **silently returns zero
rows** — so we positively assert the context took effect and fail loud (criterion 4) rather
than mistake a mis-configured read for an empty source. Enum columns are cast to text on read
so the values match the mirror's TEXT + CHECK columns without narrowing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dis_core.errors import CustomerMasterReadError
from mirror_sync_consumer.config import MirrorSyncConfig


class CmTenant(BaseModel):
    """One ``core.tenants`` row, projected to the columns the mirror replicates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID
    name: str
    display_code: str | None = None  # nullable at source; copied as-is (D55)
    status: str
    pc_created_at: datetime
    pc_updated_at: datetime
    pc_suspended_at: datetime | None = None
    pc_terminated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CmTenant:
        return cls(
            tenant_id=row["id"],
            name=row["name"],
            display_code=row["display_code"],
            status=row["status"],
            pc_created_at=row["created_at"],
            pc_updated_at=row["updated_at"],
            pc_suspended_at=row["suspended_at"],
            pc_terminated_at=row["terminated_at"],
        )


class CmStore(BaseModel):
    """One ``core.stores`` row, projected to the columns the mirror replicates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    store_id: UUID
    tenant_id: UUID
    name: str
    store_code: str | None = None  # nullable at source; copied as-is (D55)
    status: str
    country: str
    timezone: str
    currency: str
    tax_treatment: str
    pc_created_at: datetime
    pc_updated_at: datetime
    pc_closed_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CmStore:
        return cls(
            store_id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            store_code=row["store_code"],
            status=row["status"],
            country=row["country"],
            timezone=row["timezone"],
            currency=row["currency"],
            tax_treatment=row["tax_treatment"],
            pc_created_at=row["created_at"],
            pc_updated_at=row["updated_at"],
            pc_closed_at=row["closed_at"],
        )


# Enum columns cast to text so they match the mirror's TEXT + CHECK vocab exactly.
_READ_TENANTS = text(
    """
    SELECT id, name, display_code, status::text AS status,
           created_at, updated_at, suspended_at, terminated_at
    FROM core.tenants
    """
)
_READ_STORES = text(
    """
    SELECT id, tenant_id, name, store_code, status::text AS status, country, timezone, currency,
           tax_treatment::text AS tax_treatment, created_at, updated_at, closed_at
    FROM core.stores
    """
)


def _async_url(url: str) -> str:
    """Coerce a bare ``postgresql://`` DSN to the psycopg3 async dialect.

    ``create_async_engine`` needs an async driver; ``CM_DB_URL`` may arrive bare
    (``postgresql://``). This only normalises the driver token — host/db are untouched.
    """
    prefix = "postgresql://"
    if url.startswith(prefix):
        return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def create_cm_engine(url: str) -> AsyncEngine:
    """Create the read-only async engine for the Customer Master connection."""
    return create_async_engine(_async_url(url))


def assert_cm_target(
    *,
    database: str,
    role: str,
    expected_cm_db: str,
    dis_db: str,
    trace_id: str | None = None,
) -> None:
    """Positively assert the read connection is on Customer Master, never the DIS DB.

    Pure guard (unit-testable without a DB, mirroring ``dis-rls`` ``_check_posture``):
    refuse the DIS database outright, then require the expected CM database name.
    """
    if database == dis_db:
        raise CustomerMasterReadError(
            f"CM read connection is on the DIS database {database!r}; refusing to read "
            "identity from the write target",
            database=database,
            role=role,
            trace_id=trace_id,
        )
    if database != expected_cm_db:
        raise CustomerMasterReadError(
            f"CM read connection is on {database!r}, expected the Customer Master database "
            f"{expected_cm_db!r}",
            database=database,
            role=role,
            trace_id=trace_id,
        )


def assert_platform_context(
    user_type: str | None,
    *,
    database: str | None = None,
    role: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Assert the platform read context took effect in the read transaction.

    Pure guard. Under CM FORCE RLS a missing/wrong ``app.user_type`` silently returns
    zero rows, so this raises loudly before any mirror write (criterion 4).
    """
    if user_type != "PLATFORM":
        raise CustomerMasterReadError(
            f"platform read context did not take effect: app.user_type={user_type!r}, "
            "expected 'PLATFORM' (under CM FORCE RLS the read would silently return zero rows)",
            database=database,
            role=role,
            user_type=user_type,
            trace_id=trace_id,
        )


async def read_customer_master(
    engine: AsyncEngine,
    config: MirrorSyncConfig,
    *,
    trace_id: UUID,
) -> tuple[list[CmTenant], list[CmStore]]:
    """Read all tenants and stores from Customer Master under the platform context.

    One read-only transaction: assert target, set the GUCs, assert the context took
    effect, then read. Raises :class:`CustomerMasterReadError` on a wrong target or an
    ineffective context — before any caller writes.
    """
    tid = str(trace_id)
    async with engine.connect() as conn:
        async with conn.begin():
            target = (await conn.execute(text("SELECT current_database() AS db, current_user AS role"))).one()
            assert_cm_target(
                database=target.db,
                role=target.role,
                expected_cm_db=config.cm_db_name,
                dis_db=config.dis_db_name,
                trace_id=tid,
            )
            # Platform read context, transaction-local (read contract §5). NULL tenant_id is
            # the canonical PLATFORM-not-impersonating form.
            await conn.execute(text("SELECT set_config('app.user_type', 'PLATFORM', true)"))
            await conn.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": None})
            user_type = (
                await conn.execute(text("SELECT current_setting('app.user_type', true) AS ut"))
            ).scalar_one()
            assert_platform_context(user_type, database=target.db, role=target.role, trace_id=tid)
            tenant_rows = (await conn.execute(_READ_TENANTS)).mappings().all()
            store_rows = (await conn.execute(_READ_STORES)).mappings().all()
    tenants = [CmTenant.from_row(dict(r)) for r in tenant_rows]
    stores = [CmStore.from_row(dict(r)) for r in store_rows]
    return tenants, stores
