"""config.source_mappings template grain + RLS (Slice 14a acceptance criteria).

Grain half (the 0005 keys, exercised through real INSERTs):
  * One ACTIVE per (tenant, source, template): a second ACTIVE for the same
    triple is rejected; sales-ACTIVE and inventory-ACTIVE coexist under one
    source (the point of the rekey).
  * version_seq_per_source sequences PER TEMPLATE: two templates under one
    source each start at 1; a template's next version increments independently.
  * template_name maps to at most one template among non-DEPRECATED rows
    (ex_csm_template_name_per_source): a second template reusing a live name
    is rejected; version rows of ONE template share the name freely; a
    DEPRECATED row frees its name for a new template.

RLS half (modeled on tests/integration/test_rls_isolation.py — the Slice 4
proof shape):
  * Writes go through ``rls_session`` (exercising the policy WITH CHECK); the
    read-backs use a RAW connection with manual ``set_config`` — independent
    of the helper, so the test does not merely agree with itself.
  * Symmetric exact-content check between tenants A and B.
  * Negative control: unset GUC reads ZERO rows (the missing-ok
    ``current_setting`` mechanism, proving the policy is live).
  * Role posture (NOSUPERUSER/NOBYPASSRLS) and FORCE posture asserted, so the
    isolation cannot weaken silently.
  * The label view ``source_mappings_v`` is security_invoker: it follows the
    querying role's GUC scope instead of bypassing with owner rights.

WRITE scope: ``ithina_dis_db`` (5433) only; the dis-rls target guard refuses
anything else. Test rows use the ``slice14a_`` source prefix and are removed
by the cleanup fixture (admin role), keeping the migration-cycle test's
single-template downgrade precondition intact.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

import dis_testing.fixtures as fx
from dis_core.ids import new_uuid7
from dis_rls import create_rls_engine, rls_session

pytestmark = pytest.mark.integration

_TENANT_A = str(fx.TENANTS[0].uuid)
_TENANT_B = str(fx.TENANTS[1].uuid)

_RULES = json.dumps({"version": 1, "rename": {}, "normalize": {}, "cast": {}, "derive": {}})

_INSERT = text(
    """
    INSERT INTO config.source_mappings
        (tenant_id, source_id, template_id, template_name, template_type, status, mapping_rules,
         activated_at, deprecated_at)
    VALUES
        (CAST(:tid AS uuid), :source_id, CAST(:template_id AS uuid), :template_name,
         'sales', :status,
         CAST(:rules AS JSONB),
         CASE WHEN :status IN ('ACTIVE', 'DEPRECATED') THEN now() END,
         CASE WHEN :status = 'DEPRECATED' THEN now() END)
    RETURNING mapping_version_id, version_seq_per_source
    """
)


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the Slice 14a grain/RLS tests refuse to skip "
            "silently. Bring up the stack (make run-local) and export POSTGRES_URL "
            "(5433 / ithina_dis_db)."
        )

    # Identity FK targets (tenants A and B) come from the Slice 2 seeder; idempotent.
    from dis_testing.seed import seed_default_fixtures

    try:
        seed_default_fixtures(url=url)
    except Exception as exc:  # noqa: BLE001 — stack down → ERROR loudly, never skip
        raise StackRequiredError(
            f"DIS Postgres unreachable for the Slice 14a grain/RLS tests ({exc!r}); "
            "refusing to skip. Bring up the stack (make run-local)."
        ) from exc

    eng = create_rls_engine(url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def cleanup_test_mappings() -> Iterator[None]:
    """Remove this module's rows afterward (admin role — RLS-exempt by design).

    Cleanup keeps the (tenant, source) groups single-template so the 0005
    downgrade precondition in test_migration_0005 stays satisfiable.
    """
    admin_url = os.environ.get("POSTGRES_ADMIN_URL")
    if not admin_url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the grain tests need the admin role "
            "for row cleanup. Bring up the stack (make run-local)."
        )
    yield
    eng: Engine = create_engine(admin_url)
    try:
        with eng.begin() as conn:
            conn.execute(text(r"DELETE FROM config.source_mappings WHERE source_id LIKE 'slice14a\_%'"))
    finally:
        eng.dispose()


async def _insert(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    source_id: str,
    template_id: str,
    template_name: str,
    status: str = "DRAFT",
) -> int:
    """Insert through rls_session (the WITH CHECK path); returns the seq set
    by the trg_csm_set_version_seq trigger (seq omitted → NULL → trigger)."""
    async with rls_session(engine, tenant_id) as conn:
        row = (
            await conn.execute(
                _INSERT,
                {
                    "tid": tenant_id,
                    "source_id": source_id,
                    "template_id": template_id,
                    "template_name": template_name,
                    "status": status,
                    "rules": _RULES,
                },
            )
        ).one()
    return int(row.version_seq_per_source)


async def _visible_rows(engine: AsyncEngine, tenant_id: str | None, *, relation: str) -> set[str]:
    """Read visible source_ids through a RAW connection (NOT rls_session).

    The unset-GUC case (``tenant_id=None``) needs a VIRGIN session: once any
    transaction on a pooled connection has run ``set_config(..., true)``, the
    GUC placeholder reverts to ``''`` (not NULL) for that session, and
    ``''::uuid`` errors instead of matching zero rows — still fail-closed, but
    the zero-rows negative control (the Slice 4 shape) is the virgin-session
    behaviour, so the pool is disposed first.
    """
    if tenant_id is None:
        await engine.dispose()
    async with engine.connect() as conn:
        async with conn.begin():
            if tenant_id is not None:
                await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            rows = (
                (
                    await conn.execute(
                        text(
                            f"SELECT source_id FROM {relation} "  # noqa: S608 — fixed relation names
                            "WHERE source_id LIKE 'slice14a\\_%'"
                        )
                    )
                )
                .scalars()
                .all()
            )
    return set(rows)


# ---------------------------------------------------------------------------
# Grain: active-uniqueness per (tenant, source, template).
# ---------------------------------------------------------------------------


async def test_second_active_for_same_template_rejected(
    engine: AsyncEngine, cleanup_test_mappings: None
) -> None:
    source = "slice14a_active_dup"
    template = str(new_uuid7())
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source,
        template_id=template,
        template_name="sales",
        status="ACTIVE",
    )
    with pytest.raises(IntegrityError, match="uq_csm_active_per_source"):
        await _insert(
            engine,
            tenant_id=_TENANT_A,
            source_id=source,
            template_id=template,
            template_name="sales",
            status="ACTIVE",
        )


async def test_two_templates_active_coexist_under_one_source(
    engine: AsyncEngine, cleanup_test_mappings: None
) -> None:
    # The point of the rekey: sales-active and inventory-active under ONE source.
    # The two template ids are minted here, DISTINCT by assertion — no fixture
    # adoption is in play in this module, so the proof cannot collapse onto a
    # single template.
    source = "slice14a_coexist"
    t_sales = str(new_uuid7())
    t_inventory = str(new_uuid7())
    assert t_sales != t_inventory
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source,
        template_id=t_sales,
        template_name="sales",
        status="ACTIVE",
    )
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source,
        template_id=t_inventory,
        template_name="inventory",
        status="ACTIVE",
    )
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": _TENANT_A})
            count = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM config.source_mappings "
                        "WHERE source_id = :s AND status = 'ACTIVE'"
                    ),
                    {"s": source},
                )
            ).scalar_one()
    assert count == 2


# ---------------------------------------------------------------------------
# Grain: per-template version sequencing.
# ---------------------------------------------------------------------------


async def test_version_seq_sequences_per_template(engine: AsyncEngine, cleanup_test_mappings: None) -> None:
    source = "slice14a_seq"
    t_sales = str(new_uuid7())
    t_inventory = str(new_uuid7())
    assert t_sales != t_inventory  # distinct templates — the grain under test

    # Two templates under one source: EACH starts at 1 (the old
    # (tenant, source) grain would have forced 1 then 2).
    assert (
        await _insert(
            engine,
            tenant_id=_TENANT_A,
            source_id=source,
            template_id=t_sales,
            template_name="sales",
        )
        == 1
    )
    assert (
        await _insert(
            engine,
            tenant_id=_TENANT_A,
            source_id=source,
            template_id=t_inventory,
            template_name="inventory",
        )
        == 1
    )
    # And one template's lineage increments independently of the other's.
    assert (
        await _insert(
            engine,
            tenant_id=_TENANT_A,
            source_id=source,
            template_id=t_sales,
            template_name="sales",
        )
        == 2
    )


# ---------------------------------------------------------------------------
# Grain: template_name-to-template uniqueness (the EXCLUDE constraint).
# ---------------------------------------------------------------------------


async def test_name_reuse_by_a_different_template_rejected(
    engine: AsyncEngine, cleanup_test_mappings: None
) -> None:
    source = "slice14a_name_clash"
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source,
        template_id=str(new_uuid7()),
        template_name="sales",
    )
    with pytest.raises(IntegrityError, match="ex_csm_template_name_per_source"):
        await _insert(
            engine,
            tenant_id=_TENANT_A,
            source_id=source,
            template_id=str(new_uuid7()),
            template_name="sales",
        )


async def test_version_rows_of_one_template_share_the_name(
    engine: AsyncEngine, cleanup_test_mappings: None
) -> None:
    # The reason the constraint is an EXCLUDE and not a plain unique index:
    # ACTIVE v1 + DRAFT v2 of the SAME template legitimately carry one name.
    source = "slice14a_same_template"
    template = str(new_uuid7())
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source,
        template_id=template,
        template_name="sales",
        status="ACTIVE",
    )
    seq = await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source,
        template_id=template,
        template_name="sales",
        status="DRAFT",
    )
    assert seq == 2


async def test_deprecated_row_frees_its_name(engine: AsyncEngine, cleanup_test_mappings: None) -> None:
    source = "slice14a_name_freed"
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source,
        template_id=str(new_uuid7()),
        template_name="sales",
        status="DEPRECATED",
    )
    # A NEW template may take the name once the old holder is DEPRECATED
    # (mirrors how active-uniqueness frees the active slot).
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source,
        template_id=str(new_uuid7()),
        template_name="sales",
    )


# ---------------------------------------------------------------------------
# RLS: posture + isolation (the Slice 4 proof shape, on source_mappings).
# ---------------------------------------------------------------------------


async def test_force_rls_posture_holds(engine: AsyncEngine) -> None:
    async with rls_session(engine, _TENANT_A) as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE oid = 'config.source_mappings'::regclass"
                )
            )
        ).one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True, (
        "config.source_mappings is no longer FORCE row security — the table owner "
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


async def test_tenant_isolation_is_symmetric(engine: AsyncEngine, cleanup_test_mappings: None) -> None:
    source_a = "slice14a_rls_a"
    source_b = "slice14a_rls_b"
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source_a,
        template_id=str(new_uuid7()),
        template_name="default",
    )
    await _insert(
        engine,
        tenant_id=_TENANT_B,
        source_id=source_b,
        template_id=str(new_uuid7()),
        template_name="default",
    )

    a_visible = await _visible_rows(engine, _TENANT_A, relation="config.source_mappings")
    b_visible = await _visible_rows(engine, _TENANT_B, relation="config.source_mappings")

    assert source_a in a_visible and source_b not in a_visible, "tenant A leaked B's mapping"
    assert source_b in b_visible and source_a not in b_visible, "tenant B leaked A's mapping"


async def test_no_tenant_context_reads_zero_rows(engine: AsyncEngine, cleanup_test_mappings: None) -> None:
    # Negative control: unset GUC → current_setting(..., true) is NULL →
    # tenant_id = NULL matches nothing → zero rows. Proves the policy is live
    # (this is exactly the consumer-breaking trap the Task 0 gate ruled out).
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id="slice14a_rls_unset",
        template_id=str(new_uuid7()),
        template_name="default",
    )
    assert await _visible_rows(engine, None, relation="config.source_mappings") == set()


async def test_reverted_guc_session_stays_fail_closed(
    engine: AsyncEngine, cleanup_test_mappings: None
) -> None:
    # The OTHER unset shape: once a session has run set_config(..., true), the
    # GUC reverts to '' (not NULL) after that transaction — so a later
    # transaction on the SAME connection that never re-sets the scope gets
    # ''::uuid, which ERRORS (InvalidTextRepresentation) instead of matching
    # zero rows. Both shapes are fail-closed; this pins the ''-revert one:
    # error or empty, NEVER another tenant's rows.
    source_a = "slice14a_revert_a"
    source_b = "slice14a_revert_b"
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source_a,
        template_id=str(new_uuid7()),
        template_name="default",
    )
    await _insert(
        engine,
        tenant_id=_TENANT_B,
        source_id=source_b,
        template_id=str(new_uuid7()),
        template_name="default",
    )

    async with engine.connect() as conn:
        # Transaction 1: scoped to tenant A — sees exactly A's row.
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": _TENANT_A})
            scoped = (
                (
                    await conn.execute(
                        text(
                            "SELECT source_id FROM config.source_mappings "
                            "WHERE source_id LIKE 'slice14a\\_revert\\_%'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert set(scoped) == {source_a}

        # Transaction 2, SAME connection, scope never re-set: the GUC has
        # reverted to '' for this session. Fail-closed: an error or an empty
        # result are both acceptable; tenant B's row appearing is the breach.
        try:
            async with conn.begin():
                leaked = (
                    (
                        await conn.execute(
                            text(
                                "SELECT source_id FROM config.source_mappings "
                                "WHERE source_id LIKE 'slice14a\\_revert\\_%'"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        except DBAPIError as exc:
            assert "invalid input syntax for type uuid" in str(exc)  # ''::uuid
        else:
            assert leaked == [], (
                f"reverted-GUC session returned rows {leaked} — the ''-revert path is no longer fail-closed"
            )


async def test_label_view_respects_tenant_scope(engine: AsyncEngine, cleanup_test_mappings: None) -> None:
    # security_invoker: the view follows the QUERYING role's scope. Owner-rights
    # execution (the PG15 default) would silently return every tenant's rows.
    source_a = "slice14a_view_a"
    source_b = "slice14a_view_b"
    await _insert(
        engine,
        tenant_id=_TENANT_A,
        source_id=source_a,
        template_id=str(new_uuid7()),
        template_name="default",
    )
    await _insert(
        engine,
        tenant_id=_TENANT_B,
        source_id=source_b,
        template_id=str(new_uuid7()),
        template_name="default",
    )

    a_visible = await _visible_rows(engine, _TENANT_A, relation="config.source_mappings_v")
    assert source_a in a_visible and source_b not in a_visible, "the view leaked across tenants"
    assert await _visible_rows(engine, None, relation="config.source_mappings_v") == set()
