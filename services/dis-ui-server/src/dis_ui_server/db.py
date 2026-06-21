"""The ORM declarative base and the engine wiring — both through dis-rls.

This service uses the SQLAlchemy ORM / declarative layer where other DIS
services use Core/text, justified by its CRUD and system-of-record nature
(``config.source_mappings``, later slices). The layer choice carries a
decisions.md D-number assigned by the operator at the Slice-13a commit gate.

The load-bearing constraint (root CLAUDE.md hard rule 1): any future model
declared on :class:`Base` executes ONLY inside ``rls_session(engine, tenant_id)``
— never a raw ``AsyncSession``, never a second engine. The engine itself comes
from ``dis-rls`` ``create_rls_engine`` so the ``current_database()=='ithina_dis_db'``
+ NOBYPASSRLS posture guard applies to every connection this service ever opens.

Slice 13a declares no models (there are no endpoints); the base exists so later
slices attach to an already-wired foundation instead of improvising one.

Engine creation is LAZY (no connection at construction): the lifespan creates
the engine without touching the network, so an unreachable database never
blocks startup or ``/healthz`` — the first connect happens in ``/readyz`` and
degrades there to 503.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from dis_core.errors import TenantScopeError
from dis_rls import create_rls_engine, rls_platform_session, rls_session
from dis_ui_server.config import UiServerConfig


class Base(DeclarativeBase):
    """Declarative root for this service's future CRUD models.

    Every model on this base executes through the dis-rls session only
    (service CLAUDE.md durable invariant). No models exist in Slice 13a.
    """


def create_engine_from_config(config: UiServerConfig) -> AsyncEngine:
    """Create the (lazy) DIS engine; the caller owns and disposes it.

    Delegates to ``create_rls_engine`` so the wrong-database / bypassing-role
    refusal (dis-rls posture guard) covers this service structurally.
    """
    return create_rls_engine(config.postgres_url)


# ---- Slice 17b: two-GUC scope-aware session openers ----
#
# Primitive args (bool / UUID), NOT the auth ``ReadScope``/``WriteScope`` value objects,
# so this module never imports ``auth.scope`` (which would form db -> auth -> ... a cycle
# with no benefit). Callers translate their resolved scope into these primitives.


@asynccontextmanager
async def read_session(
    engine: AsyncEngine, *, is_platform: bool, tenant_id: UUID | None
) -> AsyncIterator[AsyncConnection]:
    """Open the read session for a resolved read scope (Slice 17b).

    PLATFORM see-all -> ``rls_platform_session(engine, None)`` (every tenant, via the
    policy USING branch). A pinned TENANT scope -> ``rls_session(engine, tenant_id)``
    (the unchanged single-tenant path). A PLATFORM read NEVER opens a TENANT-mode
    ``rls_session`` — the session mode follows the verified posture.
    """
    if is_platform:
        async with rls_platform_session(engine, None) as conn:
            yield conn
        return
    if tenant_id is None:  # unreachable: a pinned scope always carries a UUID
        raise TenantScopeError("a pinned read scope carries no tenant", tenant_id=None)
    async with rls_session(engine, tenant_id) as conn:
        yield conn


@asynccontextmanager
async def write_session(
    engine: AsyncEngine, *, is_platform: bool, acted_for: UUID
) -> AsyncIterator[AsyncConnection]:
    """Open the write session for the resolved acted-for tenant (Slice 17b).

    A PLATFORM actor (impersonation) -> ``rls_platform_session(engine, acted_for)``:
    see-all reads within the transaction, writes pinned to ``acted_for`` by the policy
    WITH CHECK. A TENANT actor -> ``rls_session(engine, acted_for)`` (its own tenant). The
    acted-for tenant is resolved by ``resolve_acted_for`` from the verified posture — a
    PLATFORM identity never silently opens a TENANT-mode session.
    """
    if is_platform:
        async with rls_platform_session(engine, acted_for) as conn:
            yield conn
    else:
        async with rls_session(engine, acted_for) as conn:
            yield conn
