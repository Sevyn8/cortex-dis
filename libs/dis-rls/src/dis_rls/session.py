"""The tenant-scoped async session: open a transaction, set the per-tenant scope,
run the caller's statements under that scope, commit or roll back.

Why ``AsyncConnection`` (not ``AsyncSession``): ``dis-canonical`` is pure Pydantic,
not ORM, and the streaming consumer (Slice 10) and CSV worker (Slice 9) write via
core SQL. The connection is the minimal surface matching the policy expression
``tenant_id = current_setting('app.tenant_id', true)::uuid``.

Why ``set_config(..., true)`` rather than ``SET LOCAL app.tenant_id = ...``: the GUC
value must be parameterised (it is a tenant UUID), and ``SET LOCAL`` cannot bind a
parameter. ``set_config(name, value, is_local => true)`` is the parameterisable,
transaction-local equivalent.

Role posture is **explicit, not assumed** (slice constraint): RLS is silently void
for a SUPERUSER or BYPASSRLS role, so the first session opened on an engine verifies
that the connection reached ``ithina_dis_db`` and that the connected role can NOT
bypass RLS, raising :class:`RlsContextError` otherwise.

Two session modes (Slice 17b, two-GUC ``app.user_type`` + ``app.tenant_id``):
:func:`rls_session` is the TENANT path — it sets ``app.user_type='TENANT'``
(positively, never absent-by-default) plus the tenant scope. :func:`rls_platform_session`
is the PLATFORM path — ``app.user_type='PLATFORM'`` with an empty (no-tenant, see-all) or
concrete (impersonation) ``app.tenant_id``. Both route through the SAME first-use posture
guard (the shared :func:`_verified_transaction` scaffold) so a PLATFORM session can never
bypass the bypass-role / wrong-database check.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID
from weakref import WeakSet

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from dis_core.errors import RlsContextError
from dis_core.logging import get_logger

_SERVICE = "dis-rls"

# The DIS database. Customer Master is a *different* database (ithina_platform_db)
# on a different port; current_database() is the reliable discriminator. A port
# check is useless here: under docker port-mapping inet_server_port() reports the
# container-internal 5432 even for a host connection over 5433.
_EXPECTED_DATABASE = "ithina_dis_db"

# Engines whose target + role posture have been verified once. WeakSet so a disposed
# engine is collected; this holds no connections and is not loop-bound.
_VERIFIED_ENGINES: WeakSet[AsyncEngine] = WeakSet()

_log = get_logger(_SERVICE)


def create_rls_engine(url: str | None = None) -> AsyncEngine:
    """Create an async engine for the DIS database, connecting as the service role.

    ``url`` defaults to ``POSTGRES_URL`` (the NOSUPERUSER/NOBYPASSRLS
    ``ithina_dis_user`` role). No silent default for a missing required value
    (root CLAUDE.md code-quality rule 4): a missing URL raises.
    """
    resolved = url or os.environ.get("POSTGRES_URL")
    if not resolved:
        raise RlsContextError(
            "POSTGRES_URL is not set and no url was passed to create_rls_engine",
        )
    return create_async_engine(resolved)


def _check_posture(*, database: str, role: str, rolsuper: bool, rolbypassrls: bool) -> None:
    """Pure guard: raise unless the target DB and role posture are safe for RLS.

    Factored out so the decision is unit-testable without a second database: feeding
    a Customer-Master-shaped target (``database='ithina_platform_db'``) or a
    bypassing role must raise. This is what makes the wrong target *impossible*, not
    merely unlikely.
    """
    if database != _EXPECTED_DATABASE:
        raise RlsContextError(
            f"dis-rls refuses database {database!r}; expected {_EXPECTED_DATABASE!r} "
            "(DIS on 5433, never Customer Master)",
            database=database,
            role=role,
        )
    if rolsuper or rolbypassrls:
        raise RlsContextError(
            f"dis-rls role {role!r} can bypass RLS "
            f"(rolsuper={rolsuper}, rolbypassrls={rolbypassrls}); "
            "tenant isolation would be silently void",
            database=database,
            role=role,
        )


async def _verify_target_and_role(conn: AsyncConnection) -> None:
    """Refuse the wrong database or an RLS-bypassing role. Runs once per engine."""
    row = (
        await conn.execute(
            text(
                "SELECT current_database() AS db, current_user AS role, "
                "rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        )
    ).one()
    _check_posture(
        database=row.db,
        role=row.role,
        rolsuper=row.rolsuper,
        rolbypassrls=row.rolbypassrls,
    )


@asynccontextmanager
async def _verified_transaction(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Open a transaction on the DIS engine, verifying target + role posture on the
    FIRST use of the engine, and yield the connection for the caller to scope.

    The shared scaffold for BOTH session modes: TENANT (:func:`rls_session`) and
    PLATFORM (:func:`rls_platform_session`) route through here, so the PLATFORM path
    can never open a connection that skips the first-use ``_verify_target_and_role``
    guard — a guard-bypass would make RLS silently void. The caller sets the
    per-session GUCs (``app.user_type`` and ``app.tenant_id``) inside the yielded
    transaction; it commits on clean exit and rolls back on exception.
    """
    async with engine.connect() as conn:
        async with conn.begin():
            if engine not in _VERIFIED_ENGINES:
                await _verify_target_and_role(conn)
                _VERIFIED_ENGINES.add(engine)
            yield conn


@asynccontextmanager
async def rls_session(engine: AsyncEngine, tenant_id: UUID | str) -> AsyncIterator[AsyncConnection]:
    """Open a TENANT-scoped transaction and yield the connection.

    On the first use of ``engine`` the target database and role posture are verified
    (shared scaffold). ``app.user_type='TENANT'`` is set UNCONDITIONALLY (positively,
    never absent-by-default) alongside the per-tenant scope, both transaction-locally;
    the transaction commits on clean exit and rolls back on exception.

    The caller supplies ``tenant_id`` from authenticated upstream context, never
    from a request body (lib CLAUDE.md rule).
    """
    tid = str(tenant_id)
    log = _log.bind(stage="rls_session", tenant_id=tid)
    async with _verified_transaction(engine) as conn:
        await conn.execute(text("SELECT set_config('app.user_type', 'TENANT', true)"))
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": tid},
        )
        log.debug("rls scope set")
        yield conn


@asynccontextmanager
async def rls_platform_session(
    engine: AsyncEngine, tenant_id: UUID | None = None
) -> AsyncIterator[AsyncConnection]:
    """Open a PLATFORM-scoped transaction and yield the connection (Slice 17b).

    Sets ``app.user_type='PLATFORM'`` (reads widen to all tenants via the policy USING
    branch). The acted-for tenant determines write scope, NOT read scope:

    - ``tenant_id=None`` → PLATFORM no-tenant (see-all reads, writes NOTHING): the tenant
      GUC is set to ``''``; the policy ``NULLIF(..., '')::uuid`` maps it to NULL so it
      matches no row in WITH CHECK.
    - concrete ``tenant_id`` → PLATFORM impersonation: see-all reads, writes ONLY that
      tenant (WITH CHECK pins it).

    Same first-use posture guard as :func:`rls_session` via the shared scaffold — a
    PLATFORM session never bypasses ``_check_posture``. The caller supplies the PLATFORM
    posture and (for impersonation) the acted-for tenant from VERIFIED upstream context
    (the dis-ui-server PLATFORM-gated path), never from an unverified request field.
    """
    tid = "" if tenant_id is None else str(tenant_id)
    log = _log.bind(stage="rls_platform_session", tenant_id=tid or "<see-all>")
    async with _verified_transaction(engine) as conn:
        await conn.execute(text("SELECT set_config('app.user_type', 'PLATFORM', true)"))
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": tid},
        )
        log.debug("platform rls scope set")
        yield conn
