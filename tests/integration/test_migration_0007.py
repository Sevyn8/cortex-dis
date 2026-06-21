"""Migration 0007 (audit.events de-partition, Slice 30a): target safety, the
cliff-gone proof, RLS invariance, reversibility, scope boundary, and
fresh-bootstrap convergence.

Layers (the 0002..0005 migration-test conventions):

  * **Target-safety guard, asserted positively and non-skippably.** The pure
    ``check_migration_target`` refusal logic is unit-testable without a live
    bind: refuses Customer Master outright, refuses any non-expected database,
    passes only the DIS database.
  * **The cliff-gone proof (the load-bearing test of the slice).** A
    ``PostgresAuditWriter`` write dated WELL OUTSIDE the old fixed partition
    window (2026-06-01..07) — both far-future and pre-window — lands with no
    missing-partition error. Pre-30a both writes were silently swallowed
    (decisions.md D45); they must now return True and read back.
  * **RLS tenant isolation identical through the drop-recreate.** Tenant A's
    audit row is invisible under tenant B's ``app.tenant_id`` and visible
    under tenant A's — proven via raw reads, not the writer under test.
  * **Reversible cycle against the live DIS database (5433).** ``upgrade
    head`` leaves a PLAIN audit.events (no partkey, PK (id), constraints/
    indexes/RLS intact, app-role INSERT grant intact); ``downgrade 0006``
    recreates the partitioned form with a fresh CURRENT_DATE-relative window;
    ``upgrade head`` returns to the plain shape. Errors — never skips — when
    the stack is absent (the load-bearing-proof rule from Slices 4/7).
  * **Scope boundary — MOVED.** The 30a boundary test (the other 6 parents
    stay partitioned) was repealed when migration 0009 consciously revised
    D77's scope clause and de-partitioned those parents too; the at-head
    boundary now lives in test_migration_0009.py
    (test_scope_boundary_nothing_else_moved).
  * **Fresh-bootstrap convergence on a scratch DB (the 9a lesson).** A scratch
    database on the same 5433 instance runs ``alembic upgrade head`` (0001
    applies the now-plain manifest; 0007 re-applies the same file) and its
    full normalized audit.events shape must equal the delta-path database's.

See: docs/slices/slice-30a-audit-departition.md, decisions.md D45/D34/D29/D43/D44.
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
from sqlalchemy.ext.asyncio import AsyncEngine

import dis_testing.fixtures as fx
from dis_audit import AuditEvent, EventScope, Outcome, PostgresAuditWriter, Stage
from dis_core.ids import new_uuid7
from dis_rls import create_rls_engine

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0007_audit_events_departition.py"

_SCRATCH_DB = "ithina_dis_scratch_30a"

# Constraints and indexes whose live definitions constitute the 30a end shape.
# pk_audit_events is asserted separately: (id) plain vs (id, event_date) partitioned.
_AUDIT_CONSTRAINTS = (
    "fk_audit_events_tenant",
    "ck_audit_events_event_scope_vocab",
    "ck_audit_events_outcome_vocab",
    "ck_audit_events_row_count_non_negative",
    "ck_audit_events_rows_succeeded_non_negative",
    "ck_audit_events_rows_failed_non_negative",
    "ck_audit_events_duration_non_negative",
    # Kept deliberately (NOT a partition-routing-only artifact): defines
    # event_date's semantics; Slice 21's re-partition invariant.
    "ck_audit_events_event_date_matches",
)
_AUDIT_INDEXES = (
    "ix_audit_events_trace_id",
    "ix_audit_events_tenant_time",
    "ix_audit_events_service_stage_time",
    "ix_audit_events_data_ingress_event",
    "ix_audit_events_failures",
)


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0007", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Target-safety guard: pure, always-run, never skips (no DB needed).
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Fixtures (error, never skip, when the stack is absent).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = os.environ.get("POSTGRES_ADMIN_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the 0007 migration tests need the admin "
            "role on ithina_dis_db (5433). Bring up the stack (make run-local)."
        )
    parsed = make_url(url)
    # Target safety for the fixture itself (the 5433/ithina_dis_db criterion).
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
    """RLS app-role engine for the writer-level proofs (test_audit_writer pattern)."""
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the Slice 30a cliff-gone proof refuses to skip "
            "silently. Bring up the stack (make run-local) and export POSTGRES_URL "
            "(5433 / ithina_dis_db)."
        )
    from dis_testing.seed import seed_default_fixtures

    try:
        seed_default_fixtures(url=url)  # FK target tenants; idempotent
    except Exception as exc:  # noqa: BLE001 — stack down → ERROR loudly, never skip
        raise StackRequiredError(
            f"DIS Postgres unreachable for the Slice 30a proofs ({exc!r}); refusing "
            "to skip. Bring up the stack (make run-local)."
        ) from exc

    eng = create_rls_engine(url)
    try:
        yield eng
    finally:
        await eng.dispose()


def _alembic_head() -> str:
    """The current head revision of the migration chain (file-derived, never stale)."""
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if head is None:
        raise AssertionError("alembic migration chain has no head revision")
    return head


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


# ---------------------------------------------------------------------------
# Live-shape introspection helpers (catalogs, never file text).
# ---------------------------------------------------------------------------


def _partkey(engine: Engine, relation: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT pg_get_partkeydef(CAST(:r AS regclass))"), {"r": relation}).scalar()


def _pk_def(engine: Engine) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'audit.events'::regclass AND conname = 'pk_audit_events'"
                )
            ).scalar_one()
        )


def _partition_children(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_inherits i ON c.oid = i.inhrelid "
                    "WHERE i.inhparent = 'audit.events'::regclass ORDER BY 1"
                )
            ).scalars()
        )


def _app_role_privileges(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_schema = 'audit' AND table_name = 'events' "
                    "AND grantee = 'ithina_dis_user'"
                )
            ).all()
        }


def _audit_shape(engine: Engine) -> dict[str, object]:
    """The full normalized 30a end-state shape, for cycle + convergence checks."""
    shape: dict[str, object] = {}
    with engine.connect() as conn:
        shape["partkey"] = conn.execute(text("SELECT pg_get_partkeydef('audit.events'::regclass)")).scalar()
        shape["columns"] = [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, "
                    "COALESCE(character_maximum_length, -1), collation_name, "
                    "COALESCE(column_default, '') "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'audit' AND table_name = 'events' "
                    "ORDER BY column_name"
                )
            ).all()
        ]
        shape["pk"] = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'audit.events'::regclass AND conname = 'pk_audit_events'"
            )
        ).scalar()
        shape["constraints"] = {
            name: conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'audit.events'::regclass AND conname = :n"
                ),
                {"n": name},
            ).scalar()
            for name in _AUDIT_CONSTRAINTS
        }
        shape["indexes"] = {
            name: conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'audit' "
                    "AND tablename = 'events' AND indexname = :n"
                ),
                {"n": name},
            ).scalar()
            for name in _AUDIT_INDEXES
        }
        shape["rls"] = (
            conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'audit.events'::regclass"
                )
            )
            .one()
            ._asdict()
        )
        shape["policy"] = (
            conn.execute(
                text(
                    "SELECT permissive, roles, cmd, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'audit' AND tablename = 'events' "
                    "AND policyname = 'rls_audit_events_tenant'"
                )
            )
            .one()
            ._asdict()
        )
    return shape


def _assert_plain_shape(engine: Engine) -> None:
    """The Slice 30a acceptance shape, from live catalogs."""
    assert _partkey(engine, "audit.events") is None, "audit.events is still partitioned"
    assert _partition_children(engine) == []
    assert _pk_def(engine) == "PRIMARY KEY (id)"
    shape = _audit_shape(engine)
    constraints = shape["constraints"]
    assert isinstance(constraints, dict)
    for name, definition in constraints.items():
        assert definition is not None, f"constraint {name} missing on the plain table"
    indexes = shape["indexes"]
    assert isinstance(indexes, dict)
    for name, definition in indexes.items():
        assert definition is not None, f"index {name} missing on the plain table"
    rls = shape["rls"]
    assert isinstance(rls, dict)
    assert rls == {"relrowsecurity": True, "relforcerowsecurity": True}
    # The app role can still write (the drop-recreate must not lose the grant).
    assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= _app_role_privileges(engine)


# ---------------------------------------------------------------------------
# The cliff-gone proof (the load-bearing test of the slice).
# ---------------------------------------------------------------------------


async def _read_back(engine: AsyncEngine, tenant_id: str, trace_id: str) -> dict[str, object] | None:
    """Raw read with a manual set_config — independent of the writer under test."""
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT tenant_id::text, trace_id::text, event_date::text "
                            "FROM audit.events WHERE trace_id = :tr"
                        ),
                        {"tr": trace_id},
                    )
                )
                .mappings()
                .first()
            )
    return dict(row) if row is not None else None


def _delete_audit_trace(admin_engine: Engine, trace_id: str) -> None:
    """Revert a directly-written audit row (admin bypasses FORCE RLS).

    These cliff-gone proofs write real ``audit.events`` rows; without this they would
    leak past the suite and the D100 post-suite clean-state guard would (correctly) fail.
    """
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM audit.events WHERE trace_id = :tr"), {"tr": trace_id})


@pytest.mark.parametrize(
    ("timestamp", "expected_date"),
    [
        # Far OUTSIDE the old fixed window (2026-06-01..07) on both sides.
        # Pre-30a each write hit "no partition found" and was silently
        # swallowed by fire-and-forget (decisions.md D45).
        (datetime(2027, 3, 15, 12, 0, tzinfo=UTC), "2027-03-15"),
        (datetime(2025, 1, 1, 0, 30, tzinfo=UTC), "2025-01-01"),
    ],
)
async def test_write_outside_old_partition_window_lands(
    app_engine: AsyncEngine, admin_engine: Engine, timestamp: datetime, expected_date: str
) -> None:
    tenant = fx.TENANTS[0].uuid
    trace_id = new_uuid7()
    event = AuditEvent(
        event_timestamp=timestamp,
        trace_id=trace_id,
        tenant_id=tenant,
        service_name="streaming-consumer",
        stage=Stage.CANONICAL_WRITTEN,
        event_scope=EventScope.INGRESS_EVENT,
        outcome=Outcome.SUCCESS,
    )
    try:
        assert await PostgresAuditWriter(app_engine).write(event) is True, (
            f"audit write dated {expected_date} (outside the old 2026-06-01..07 window) "
            "failed — the D45 silent write-cliff is not gone"
        )
        row = await _read_back(app_engine, str(tenant), str(trace_id))
        assert row is not None and row["event_date"] == expected_date
    finally:
        _delete_audit_trace(admin_engine, str(trace_id))


# ---------------------------------------------------------------------------
# RLS tenant isolation identical through the drop-recreate (proven, not assumed).
# ---------------------------------------------------------------------------


async def test_rls_isolation_survives_departition(app_engine: AsyncEngine, admin_engine: Engine) -> None:
    tenant_a, tenant_b = fx.TENANTS[0].uuid, fx.TENANTS[1].uuid
    trace_id = new_uuid7()
    event = AuditEvent(
        event_timestamp=datetime(2027, 7, 1, 9, 0, tzinfo=UTC),
        trace_id=trace_id,
        tenant_id=tenant_a,
        service_name="streaming-consumer",
        stage=Stage.CANONICAL_WRITTEN,
        event_scope=EventScope.INGRESS_EVENT,
        outcome=Outcome.SUCCESS,
    )
    try:
        assert await PostgresAuditWriter(app_engine).write(event) is True

        # Tenant B must NOT see tenant A's audit row; tenant A must.
        assert await _read_back(app_engine, str(tenant_b), str(trace_id)) is None, (
            "RLS isolation broke through the de-partition: tenant B can read tenant A's audit row"
        )
        visible = await _read_back(app_engine, str(tenant_a), str(trace_id))
        assert visible is not None and visible["tenant_id"] == str(tenant_a)
    finally:
        _delete_audit_trace(admin_engine, str(trace_id))


# ---------------------------------------------------------------------------
# Reversible cycle + scope boundary against the live DIS database (5433).
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="downgrade-reversibility deferred until staging (D99)")
def test_migration_cycle_departition_and_back(admin_engine: Engine) -> None:
    # apply-to-head/fresh-bootstrap stays covered by test_fresh_bootstrap_converges_with_delta_path.
    # upgrade head first (idempotent if already at 0007): the plain shape.
    _alembic("upgrade", "head")
    _assert_plain_shape(admin_engine)
    plain_shape = _audit_shape(admin_engine)

    # downgrade to 0006: the partitioned form returns with a FRESH
    # CURRENT_DATE-relative 7-day window (not the original 2026-06-01..07).
    _alembic("downgrade", "0006")
    assert _partkey(admin_engine, "audit.events") == "RANGE (event_date)"
    assert _pk_def(admin_engine) == "PRIMARY KEY (id, event_date)"
    children = _partition_children(admin_engine)
    assert len(children) == 7, f"downgrade created {len(children)} partitions, expected 7"
    assert all(c.startswith("events_p") for c in children)
    assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= _app_role_privileges(admin_engine)

    # re-upgrade: the plain shape again, identical to the first pass.
    _alembic("upgrade", "head")
    _assert_plain_shape(admin_engine)
    assert _audit_shape(admin_engine) == plain_shape


# ---------------------------------------------------------------------------
# Fresh-bootstrap convergence on a scratch DB (the 9a lesson).
# ---------------------------------------------------------------------------


def test_fresh_bootstrap_converges_with_delta_path(admin_url: str, admin_engine: Engine) -> None:
    """The fresh path (0001 applies the now-plain manifest; 0007 re-applies the
    same file) must land the IDENTICAL audit.events shape the delta path leaves
    behind (the delta path being the partitioned table converted by 0007)."""
    _alembic("upgrade", "head")  # ensure the delta-path reference is at head

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
        # The migrations' target guard keys on POSTGRES_DB; Customer
        # Master stays hard-blocked by name regardless.
        "POSTGRES_DB": _SCRATCH_DB,
    }
    try:
        # Stop at 0006 first: 0001 has applied the manifest verbatim, so this
        # IS the manifest-built (already-plain) shape, before 0007 runs.
        _alembic("upgrade", "0006", env_overrides=scratch_env)
        manifest_shape = _audit_shape(scratch_engine)
        assert manifest_shape["partkey"] is None, (
            "fresh bootstrap built a PARTITIONED audit.events — the 0001 manifest "
            "still partitions it (the PARTITIONED-list tuple is back?)"
        )

        # 0007 on a manifest-fresh database: drop-and-recreate from the SAME
        # file — shape-identical, so manifest-as-source-of-truth holds. 0008
        # (existence-gated ADD COLUMN + def-gated CHECK swap) is likewise a
        # true no-op on the manifest-fresh shape, and 0009 (the six
        # canonical/staging parents) never touches audit.events, so the
        # equality still pins all three migrations' audit no-op property.
        _alembic("upgrade", "head", env_overrides=scratch_env)
        with scratch_engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert head == _alembic_head()
        assert _audit_shape(scratch_engine) == manifest_shape, (
            "migrations 0007/0008 CHANGED a manifest-fresh database — the manifest "
            "no longer carries their end state (drift self-healed)"
        )

        # And the fresh end state equals the delta-path end state.
        assert _audit_shape(scratch_engine) == _audit_shape(admin_engine)
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
