"""dis-rls — RLS-aware async Postgres session helper.

Every canonical read/write in DIS goes through :func:`rls_session`, which opens a
per-tenant scoped transaction (``SET LOCAL app.tenant_id``) so one tenant cannot
read another's rows (root CLAUDE.md hard rules 1 & 12).

The caller owns the engine (created via :func:`create_rls_engine`); there is no
hidden process-wide engine, so there is no untested cross-event-loop singleton
path. Production services create the engine in their app lifespan; tests use a
loop-scoped engine fixture.
"""

from __future__ import annotations

from dis_rls.session import create_rls_engine, rls_platform_session, rls_session

__all__ = ["create_rls_engine", "rls_platform_session", "rls_session"]
