"""Slice 17b: the PLATFORM session entry point routes through the SAME first-use posture
guard as ``rls_session`` (closes the completion-inventory gap-4: previously the guard on
the PLATFORM path was proven only by-construction).

``_check_posture`` (pure) and the ``rls_session`` posture are already unit-tested in
libs/dis-rls/tests/unit/test_session_guards.py. This proves the PLATFORM entry point
``rls_platform_session`` actually triggers that guard: opening it on a BYPASSRLS engine
RAISES ``RlsContextError`` before any tenant data is touched. It stays on
``ithina_dis_db`` (never Customer Master) and exercises the bypass-role branch.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from dis_core.errors import RlsContextError
from dis_rls import create_rls_engine, rls_platform_session

pytestmark = pytest.mark.integration


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


async def test_platform_session_refuses_a_bypassrls_role() -> None:
    # The Alembic admin role is BYPASSRLS — RLS would be silently void, so the shared
    # _verified_transaction guard must refuse the PLATFORM session exactly as it does the
    # TENANT one. POSTGRES_ADMIN_URL targets ithina_dis_db as ithina_dis_admin (bypassrls).
    admin_url = os.environ.get("POSTGRES_ADMIN_URL")
    if not admin_url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the PLATFORM-session posture-guard proof "
            "refuses to skip. Bring up the stack (make run-local)."
        )
    engine = create_rls_engine(admin_url)
    try:
        with pytest.raises(RlsContextError):
            async with rls_platform_session(engine, None) as conn:
                await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
