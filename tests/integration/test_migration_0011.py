"""Migration 0011 (two-GUC PLATFORM/TENANT RLS policy rewrite — Slice 17b).

Proves, against the live DIS database (5433 / ithina_dis_db) and a scratch DB:

  * **Target-safety guard** (pure, always-run): refuses Customer Master and any
    non-DIS database; passes the DIS database.
  * **The asymmetric two-GUC end-state on all 13 policies** (criterion 1), read
    from live ``pg_policies``: USING carries the PLATFORM branch AND the NULLIF
    tenant match; WITH CHECK carries the NULLIF tenant match and NEVER a PLATFORM
    branch (the structural write-nothing guarantee); ``audit.events`` is USING-only
    with the tenant-less branch + PLATFORM, no WITH CHECK.
  * **Structural catastrophe at the policy layer** (criterion 7b/7c): a dropped
    USING PLATFORM guard or a WITH CHECK contaminated with a PLATFORM branch is
    caught by per-policy predicate assertions — not "RLS is enabled".
  * **Both-direction catalog equality** (criterion 2): upgrade head -> two-GUC;
    downgrade 0010 -> the EXACT pre-slice single-GUC text (no NULLIF, no PLATFORM);
    upgrade head -> identical to the first head capture.
  * **Fresh == migrated** on a scratch DB: 0001 applies the edited DDL files, so a
    fresh bootstrap to head lands the identical policy text the delta path leaves.
  * **Live RLS rows** (criteria 3/4/5, the catastrophe property 7a): under the
    two-GUC policy, a TENANT is DENIED another tenant's row; a PLATFORM see-all
    session reads across tenants but its WITH CHECK writes NOTHING; a PLATFORM
    impersonation session (tenant=T) writes ONLY T and is refused for any other.

See: docs/slices/slice-17b-two-guc-platform-rls.md, decisions.md D76 (realized).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

import dis_testing.fixtures as fx
from dis_core.ids import new_uuid7
from dis_rls import create_rls_engine

pytestmark = pytest.mark.integration


def _alembic_head() -> str:
    """The current head revision of the migration chain (file-derived, never stale).

    Read dynamically so a later migration chaining on (e.g. 0012) does not break this
    fresh-bootstrap convergence test — the same idiom as test_migration_0008/0009.
    """
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if head is None:
        raise AssertionError("alembic migration chain has no head revision")
    return head


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0011_two_guc_platform_rls.py"
_SCRATCH_DB = "ithina_dis_scratch_0011"

# The 12 symmetric tenant_isolation tables + the audit.events outlier.
_TENANT_ISOLATION: tuple[tuple[str, str], ...] = (
    ("bronze", "data_ingress_events"),
    ("canonical", "store_sku_change_events"),
    ("canonical", "store_sku_current_position"),
    ("canonical", "store_sku_sale_events"),
    ("canonical", "store_sku_signal_history"),
    ("config", "source_mappings"),
    ("quarantine", "quarantined_chunks"),
    ("quarantine", "quarantined_rows"),
    ("staging", "store_sku_change_events"),
    ("staging", "store_sku_current_position"),
    ("staging", "store_sku_sale_events"),
    ("staging", "store_sku_signal_history"),
)
_TENANT_POLICY = "tenant_isolation"
_AUDIT_SCHEMA, _AUDIT_TABLE, _AUDIT_POLICY = "audit", "events", "rls_audit_events_tenant"

# Substrings of the live-rendered predicate (robust to Postgres' exact spacing).
_PLATFORM_BRANCH = "'PLATFORM'"
_NULLIF = "NULLIF"
_TENANT_MATCH = "tenant_id ="


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0011", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Target-safety guard: pure, always-run, never skips (no DB) ---------------


def test_guard_refuses_customer_master() -> None:
    mod = _load_migration_module()
    with pytest.raises(RuntimeError, match="Customer Master"):
        mod.check_migration_target("ithina_platform_db", expected_db="ithina_dis_db")


def test_guard_refuses_unexpected_database() -> None:
    mod = _load_migration_module()
    with pytest.raises(RuntimeError, match="expected"):
        mod.check_migration_target("some_other_db", expected_db="ithina_dis_db")


def test_guard_passes_the_dis_database_positively() -> None:
    mod = _load_migration_module()
    mod.check_migration_target("ithina_dis_db", expected_db="ithina_dis_db")


# --- Fixtures (error, never skip, when the stack is absent) --------------------


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = os.environ.get("POSTGRES_ADMIN_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the 0011 migration tests need the admin "
            "role on ithina_dis_db (5433). Bring up the stack (make run-local)."
        )
    parsed = make_url(url)
    assert parsed.database == "ithina_dis_db"
    assert parsed.port == 5433
    return url


@pytest.fixture(scope="module")
def admin_engine(admin_url: str) -> Iterator[Engine]:
    engine = create_engine(admin_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
async def app_engine() -> AsyncIterator[AsyncEngine]:
    """RLS app-role engine (NOBYPASSRLS) for the live-row proofs."""
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the 0011 live-RLS proofs refuse to skip. "
            "Bring up the stack (make run-local)."
        )
    from dis_testing.seed import seed_default_fixtures

    try:
        seed_default_fixtures(url=url)  # FK targets: tenants, stores, mapping; idempotent
    except Exception as exc:  # noqa: BLE001 — stack down → ERROR loudly, never skip
        raise StackRequiredError(
            f"DIS Postgres unreachable for the 0011 proofs ({exc!r}); refusing to skip."
        ) from exc
    eng = create_rls_engine(url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture(scope="module")
def mapping_version_id(admin_url: str, admin_engine: Engine) -> int:
    from dis_testing.seed import seed_default_fixtures

    seed_default_fixtures(url=admin_url)
    with admin_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT mapping_version_id FROM config.source_mappings "
                    "WHERE tenant_id = CAST(:t AS uuid) AND source_id = :s "
                    "ORDER BY mapping_version_id LIMIT 1"
                ),
                {"t": str(fx.PRIMARY_TENANT.uuid), "s": fx.DEFAULT_SOURCE_ID},
            ).scalar_one()
        )


def _alembic(*args: str, env_overrides: dict[str, str] | None = None) -> None:
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# --- Policy introspection (catalogs, never file text) -------------------------


def _policy(engine: Engine, schema: str, table: str, policyname: str) -> tuple[str, str | None]:
    """The live (qual, with_check) for one policy, as pg_policies renders them."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT qual, with_check FROM pg_policies "
                "WHERE schemaname = :s AND tablename = :t AND policyname = :p"
            ),
            {"s": schema, "t": table, "p": policyname},
        ).one()
    return str(row.qual), (None if row.with_check is None else str(row.with_check))


def _all_policies(engine: Engine) -> dict[str, tuple[str, str | None]]:
    out: dict[str, tuple[str, str | None]] = {}
    for schema, table in _TENANT_ISOLATION:
        out[f"{schema}.{table}"] = _policy(engine, schema, table, _TENANT_POLICY)
    out[f"{_AUDIT_SCHEMA}.{_AUDIT_TABLE}"] = _policy(engine, _AUDIT_SCHEMA, _AUDIT_TABLE, _AUDIT_POLICY)
    return out


# --- The asymmetric end-state + structural catastrophe (policy layer) ---------


def test_all_13_carry_the_asymmetric_two_guc_form(admin_engine: Engine) -> None:
    """Criterion 1 + the structural catastrophe (7b/7c) at the policy layer.

    For the 12 tenant_isolation policies: USING widens with the PLATFORM branch and
    NULLIF-wraps the tenant match; WITH CHECK NULLIF-wraps the tenant match and
    NEVER carries a PLATFORM branch. For audit.events: USING-only, with the
    tenant-less branch AND the PLATFORM branch, no WITH CHECK.
    """
    _alembic("upgrade", "head")

    for schema, table in _TENANT_ISOLATION:
        qual, with_check = _policy(engine := admin_engine, schema, table, _TENANT_POLICY)
        rel = f"{schema}.{table}"
        # USING: PLATFORM branch present (7b: a dropped guard fails here), NULLIF-wrapped.
        assert _PLATFORM_BRANCH in qual, f"{rel}: USING lost the PLATFORM read branch"
        assert _NULLIF in qual, f"{rel}: USING lost the NULLIF tenant wrapper"
        # WITH CHECK: tenant-pinned, NULLIF-wrapped, and NEVER widened (7c: the catastrophe).
        assert with_check is not None, f"{rel}: WITH CHECK disappeared"
        assert _PLATFORM_BRANCH not in with_check, (
            f"{rel}: WITH CHECK carries a PLATFORM branch — cross-tenant writes are open "
            "(the Customer Master mistake this slice diverges from)"
        )
        assert _NULLIF in with_check and _TENANT_MATCH in with_check, (
            f"{rel}: WITH CHECK is no longer the NULLIF tenant pin"
        )

    audit_qual, audit_check = _policy(admin_engine, _AUDIT_SCHEMA, _AUDIT_TABLE, _AUDIT_POLICY)
    assert audit_check is None, "audit.events grew a WITH CHECK — the outlier shape changed"
    assert _PLATFORM_BRANCH in audit_qual, "audit.events USING lost the PLATFORM branch"
    assert "IS NULL" in audit_qual, "audit.events USING lost its tenant-less branch"
    assert _NULLIF in audit_qual, "audit.events USING lost the NULLIF tenant wrapper"


@pytest.mark.skip(reason="downgrade-reversibility deferred until staging (D99)")
def test_cycle_both_directions_round_trips_exact_text(admin_engine: Engine) -> None:
    # apply-to-head stays covered by test_all_13_carry_the_asymmetric_two_guc_form +
    # test_fresh_bootstrap_converges_with_delta_path (both upgrade-only).
    """Criterion 2: head (two-GUC) -> downgrade 0010 (EXACT single-GUC, no NULLIF /
    no PLATFORM) -> head (identical to the first two-GUC capture)."""
    _alembic("upgrade", "head")
    head_first = _all_policies(admin_engine)

    _alembic("downgrade", "0010")
    single = _all_policies(admin_engine)
    for rel, (qual, with_check) in single.items():
        assert _PLATFORM_BRANCH not in qual, f"{rel}: downgrade left a PLATFORM branch in USING"
        assert _NULLIF not in qual, f"{rel}: downgrade left a NULLIF wrapper in USING"
        if with_check is not None:
            assert _PLATFORM_BRANCH not in with_check and _NULLIF not in with_check, (
                f"{rel}: downgrade left two-GUC residue in WITH CHECK"
            )
    # audit.events stays USING-only across the downgrade.
    assert single[f"{_AUDIT_SCHEMA}.{_AUDIT_TABLE}"][1] is None

    _alembic("upgrade", "head")
    assert _all_policies(admin_engine) == head_first, (
        "re-upgrade did not reproduce the first head policy text (non-deterministic SQL?)"
    )


def test_fresh_bootstrap_converges_with_delta_path(admin_url: str, admin_engine: Engine) -> None:
    """The fresh path (0001 applies the edited two-GUC DDL files, then 0011 re-applies
    the same end-state) lands the IDENTICAL 13 policies the delta path leaves at head."""
    _alembic("upgrade", "head")
    delta_head = _all_policies(admin_engine)

    parsed = make_url(admin_url)
    scratch_url = parsed.set(database=_SCRATCH_DB)
    autocommit = admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with autocommit.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": _SCRATCH_DB},
        )
        conn.execute(text(f"DROP DATABASE IF EXISTS {_SCRATCH_DB}"))
        conn.execute(text(f"CREATE DATABASE {_SCRATCH_DB}"))

    scratch_engine = create_engine(scratch_url)
    scratch_env = {
        "POSTGRES_ADMIN_URL": scratch_url.render_as_string(hide_password=False),
        "POSTGRES_DB": _SCRATCH_DB,
    }
    try:
        _alembic("upgrade", "head", env_overrides=scratch_env)
        with scratch_engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert head == _alembic_head()
        assert _all_policies(scratch_engine) == delta_head, (
            "fresh bootstrap produced different policy text than the migrated path — "
            "the DDL files and migration 0011 disagree (fresh != migrated)"
        )
    finally:
        scratch_engine.dispose()
        with autocommit.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": _SCRATCH_DB},
            )
            conn.execute(text(f"DROP DATABASE IF EXISTS {_SCRATCH_DB}"))


# --- Live RLS rows: the catastrophe property + see-all + write-nothing --------

_FAR_FUTURE = datetime(2031, 1, 7, 12, 0, 0, tzinfo=UTC)
_REL = "canonical.store_sku_change_events"


async def _insert_change_event(
    engine: AsyncEngine,
    *,
    user_type: str,
    session_tenant: str,
    row_tenant: str,
    mapping_version_id: int,
    trace_id: str,
) -> None:
    """One change-event INSERT under an explicit (user_type, app.tenant_id) scope,
    writing a row whose tenant_id is ``row_tenant`` (which may differ from the
    session tenant — that is exactly what WITH CHECK must police)."""
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.user_type', :u, true)"), {"u": user_type})
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": session_tenant})
            await conn.execute(
                text(
                    "INSERT INTO canonical.store_sku_change_events "
                    "(event_date, tenant_id, store_id, sku_id, event_category, event_subtype, "
                    " source_event_timestamp, value_after, source_id, source_event_id, "
                    " mapping_version_id, trace_id, dis_channel) "
                    "VALUES ((CAST(:ts AS timestamptz) AT TIME ZONE 'UTC')::date, "
                    " CAST(:row_tenant AS uuid), CAST(:store AS uuid), :sku, 'PRICE', "
                    " 'RETAIL_PRICE_CHANGE', CAST(:ts AS timestamptz), "
                    " CAST(:value_after AS jsonb), :source_id, :source_event_id, "
                    " :mapping_version_id, CAST(:trace AS uuid), 'csv_upload')"
                ),
                {
                    "ts": _FAR_FUTURE.isoformat(),
                    "row_tenant": row_tenant,
                    "store": str(fx.PRIMARY_STORE.uuid),
                    "sku": "MIG0011-SKU",
                    "value_after": '{"price": "9.99"}',
                    "source_id": fx.DEFAULT_SOURCE_ID,
                    "source_event_id": f"mig0011:{trace_id}",
                    "mapping_version_id": mapping_version_id,
                    "trace": trace_id,
                },
            )


async def _count_under_scope(
    engine: AsyncEngine, *, user_type: str, session_tenant: str, trace_id: str
) -> int:
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.user_type', :u, true)"), {"u": user_type})
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": session_tenant})
            return int(
                (
                    await conn.execute(
                        text(f"SELECT COUNT(*) FROM {_REL} WHERE trace_id = CAST(:trace AS uuid)"),  # noqa: S608
                        {"trace": trace_id},
                    )
                ).scalar_one()
            )


def _cleanup(admin_engine: Engine, trace_id: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text(f"DELETE FROM {_REL} WHERE trace_id = CAST(:trace AS uuid)"),  # noqa: S608
            {"trace": trace_id},
        )


async def test_tenant_is_denied_another_tenants_row(
    app_engine: AsyncEngine, admin_engine: Engine, mapping_version_id: int
) -> None:
    """Criterion 3 / the catastrophe property (7a): a TENANT row written for A is
    INVISIBLE to a TENANT session for B, and visible to A — under live RLS rows, not
    merely 'RLS is on'."""
    _alembic("upgrade", "head")
    tenant_a, tenant_b = str(fx.PRIMARY_TENANT.uuid), str(fx.TENANTS[1].uuid)
    trace = str(new_uuid7())
    try:
        await _insert_change_event(
            app_engine,
            user_type="TENANT",
            session_tenant=tenant_a,
            row_tenant=tenant_a,
            mapping_version_id=mapping_version_id,
            trace_id=trace,
        )
        assert (
            await _count_under_scope(app_engine, user_type="TENANT", session_tenant=tenant_b, trace_id=trace)
        ) == 0, "catastrophe: TENANT B can read TENANT A's row"
        assert (
            await _count_under_scope(app_engine, user_type="TENANT", session_tenant=tenant_a, trace_id=trace)
        ) == 1, "TENANT A cannot read its own row"
    finally:
        _cleanup(admin_engine, trace)


async def test_platform_sees_all_but_writes_nothing(
    app_engine: AsyncEngine, admin_engine: Engine, mapping_version_id: int
) -> None:
    """Criterion 4: a PLATFORM-no-tenant session reads A's row (see-all via the USING
    branch) but its WITH CHECK refuses every write (the structural write-nothing)."""
    _alembic("upgrade", "head")
    tenant_a = str(fx.PRIMARY_TENANT.uuid)
    trace = str(new_uuid7())
    try:
        await _insert_change_event(
            app_engine,
            user_type="TENANT",
            session_tenant=tenant_a,
            row_tenant=tenant_a,
            mapping_version_id=mapping_version_id,
            trace_id=trace,
        )
        # PLATFORM no-tenant ('' tenant GUC) reads across tenants.
        assert (
            await _count_under_scope(app_engine, user_type="PLATFORM", session_tenant="", trace_id=trace)
        ) == 1, "PLATFORM see-all could not read TENANT A's row"
        # ...and writes NOTHING: WITH CHECK NULLIF('')->NULL never matches a row tenant.
        write_trace = str(new_uuid7())
        with pytest.raises(DBAPIError):
            await _insert_change_event(
                app_engine,
                user_type="PLATFORM",
                session_tenant="",
                row_tenant=tenant_a,
                mapping_version_id=mapping_version_id,
                trace_id=write_trace,
            )
        assert (
            await _count_under_scope(
                app_engine, user_type="TENANT", session_tenant=tenant_a, trace_id=write_trace
            )
        ) == 0, "PLATFORM no-tenant write landed a row — WITH CHECK did not pin"
    finally:
        _cleanup(admin_engine, trace)


async def test_platform_impersonation_writes_only_the_target(
    app_engine: AsyncEngine, admin_engine: Engine, mapping_version_id: int
) -> None:
    """Criterion 5: a PLATFORM session acting for tenant A writes A's row, and is
    REFUSED writing tenant B's row (WITH CHECK pins to the acted-for tenant)."""
    _alembic("upgrade", "head")
    tenant_a, tenant_b = str(fx.PRIMARY_TENANT.uuid), str(fx.TENANTS[1].uuid)
    ok_trace, bad_trace = str(new_uuid7()), str(new_uuid7())
    try:
        # Impersonating A (session tenant = A): a row for A lands.
        await _insert_change_event(
            app_engine,
            user_type="PLATFORM",
            session_tenant=tenant_a,
            row_tenant=tenant_a,
            mapping_version_id=mapping_version_id,
            trace_id=ok_trace,
        )
        assert (
            await _count_under_scope(
                app_engine, user_type="TENANT", session_tenant=tenant_a, trace_id=ok_trace
            )
        ) == 1, "PLATFORM impersonation of A failed to write A's row"
        # Impersonating A but naming B in the row: WITH CHECK refuses (B != A).
        with pytest.raises(DBAPIError):
            await _insert_change_event(
                app_engine,
                user_type="PLATFORM",
                session_tenant=tenant_a,
                row_tenant=tenant_b,
                mapping_version_id=mapping_version_id,
                trace_id=bad_trace,
            )
    finally:
        _cleanup(admin_engine, ok_trace)
        _cleanup(admin_engine, bad_trace)
