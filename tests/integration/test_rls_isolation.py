"""RLS isolation: a tenant-scoped session cannot read another tenant's rows (AC2/AC3).

This is the load-bearing test of Slice 4. It WRITES to Postgres, so it runs only
against ``ithina_dis_db`` on 5433 (never Customer Master on 5432); the ``dis-rls``
target guard refuses anything else.

Non-vacuous by construction:
  * Writes go through ``rls_session`` (the lib under test), but read-backs use a RAW
    connection with a manual ``set_config`` — independent of the helper, so the test
    does not merely agree with itself.
  * Symmetric exact-content check: A-scope sees exactly A's row and never B's; B-scope
    sees exactly B's row and never A's. A one-direction or count-only check could pass
    while a policy asymmetry leaks the other way.
  * Negative control: with ``app.tenant_id`` unset the table returns ZERO rows —
    because the policy is ``current_setting('app.tenant_id', true)`` (missing-ok → the
    GUC is NULL → ``tenant_id = NULL`` matches nothing). That missing-ok flag is the
    mechanism behind "would fail if the scope were not set".
  * Role posture asserted independently: the connected role is NOSUPERUSER /
    NOBYPASSRLS, so isolation is not silently void.
  * FORCE posture asserted: the table is relrowsecurity AND relforcerowsecurity, so a
    later drop of FORCE cannot silently let the table owner bypass and weaken this test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import dis_testing.fixtures as fx
from dis_core.ids import new_uuid7
from dis_rls import create_rls_engine, rls_session

pytestmark = pytest.mark.integration

_TENANT_A = str(fx.TENANTS[0].uuid)
_TENANT_B = str(fx.TENANTS[1].uuid)

_INSERT = text(
    """
    INSERT INTO bronze.data_ingress_events
        (id, tenant_id, source_id, dis_channel, trace_id, gcs_uri, received_at)
    VALUES
        (:id, :tid, :source_id, 'csv_upload', :trace_id, :gcs_uri, now())
    """
)


# This is the load-bearing AC2 proof. It must NOT silently skip when the stack is
# absent — a stackless run would otherwise report green without isolation ever being
# verified. So a missing/unreachable stack is a loud ERROR, not a skip. (Scope: this
# slice's stack-dependent integration tests only; CI/stack-up orchestration is the
# guardrails slice, not here. No default-URL fallback — that would be a silent
# fallback, code-quality rule 4.)
class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the Slice 4 RLS isolation test (load-bearing "
            "AC2) refuses to skip silently. Bring up the stack (make run-local) and "
            "export POSTGRES_URL (5433 / ithina_dis_db)."
        )

    # Identity FK targets (tenants A and B) come from the Slice 2 seeder; idempotent.
    from dis_testing.seed import seed_default_fixtures

    try:
        seed_default_fixtures(url=url)
    except Exception as exc:  # noqa: BLE001 — stack down → ERROR loudly, never skip
        raise StackRequiredError(
            f"DIS Postgres unreachable for the load-bearing RLS isolation test ({exc!r}); "
            "refusing to skip. Bring up the stack (make run-local)."
        ) from exc

    eng = create_rls_engine(url)
    try:
        # Fresh engine per test, disposed inside this loop (the safe async pattern).
        yield eng
    finally:
        await eng.dispose()


async def _visible_ids(engine: AsyncEngine, tenant_id: str | None) -> set[str]:
    """Read visible ids through a RAW connection (NOT rls_session) — independent check."""
    async with engine.connect() as conn:
        async with conn.begin():
            if tenant_id is not None:
                await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            rows = (
                (await conn.execute(text("SELECT id::text FROM bronze.data_ingress_events"))).scalars().all()
            )
    return set(rows)


async def test_force_rls_posture_holds(engine: AsyncEngine) -> None:
    # Fold-in: assert FORCE so a later drop can't silently let the owner bypass RLS.
    async with rls_session(engine, _TENANT_A) as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE oid = 'bronze.data_ingress_events'::regclass"
                )
            )
        ).one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True, (
        "bronze.data_ingress_events is no longer FORCE row security — the table owner "
        "could bypass RLS and this isolation test would weaken silently"
    )


async def test_connected_role_cannot_bypass_rls(engine: AsyncEngine) -> None:
    async with rls_session(engine, _TENANT_A) as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert row.rolsuper is False
    assert row.rolbypassrls is False, "RLS is silently void for a BYPASSRLS role"


async def test_tenant_isolation_is_symmetric(engine: AsyncEngine) -> None:
    id_a = str(new_uuid7())
    id_b = str(new_uuid7())

    try:
        # Write one row per tenant THROUGH the helper (tenant-scoped writes).
        async with rls_session(engine, _TENANT_A) as conn:
            await conn.execute(
                _INSERT,
                {
                    "id": id_a,
                    "tid": _TENANT_A,
                    "source_id": "manual_csv_upload",
                    "trace_id": str(new_uuid7()),
                    "gcs_uri": "gs://test/a",
                },
            )
        async with rls_session(engine, _TENANT_B) as conn:
            await conn.execute(
                _INSERT,
                {
                    "id": id_b,
                    "tid": _TENANT_B,
                    "source_id": "manual_csv_upload",
                    "trace_id": str(new_uuid7()),
                    "gcs_uri": "gs://test/b",
                },
            )

        # Symmetric exact-content check via independent raw reads.
        a_visible = await _visible_ids(engine, _TENANT_A)
        b_visible = await _visible_ids(engine, _TENANT_B)

        assert id_a in a_visible and id_b not in a_visible, "tenant A leaked B's row (or lost its own)"
        assert id_b in b_visible and id_a not in b_visible, "tenant B leaked A's row (or lost its own)"
    finally:
        # A test that mutates the shared live DB restores it (D100): delete both bronze rows,
        # each scoped to its tenant (bronze is FORCE RLS).
        async with rls_session(engine, _TENANT_A) as conn:
            await conn.execute(text("DELETE FROM bronze.data_ingress_events WHERE id = :id"), {"id": id_a})
        async with rls_session(engine, _TENANT_B) as conn:
            await conn.execute(text("DELETE FROM bronze.data_ingress_events WHERE id = :id"), {"id": id_b})


async def test_no_tenant_context_reads_zero_rows(engine: AsyncEngine) -> None:
    # Negative control (Slice 1 smoke parity). Unset GUC → current_setting(..., true)
    # is NULL → tenant_id = NULL matches nothing → zero rows. This is exactly what
    # would fail if the scope were not set or the role could bypass RLS.
    assert await _visible_ids(engine, tenant_id=None) == set()
