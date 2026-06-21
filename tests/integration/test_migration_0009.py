"""Migration 0009 (canonical+staging event/signal de-partition — D77's scope
clause revised): target safety, the any-date proof, RLS invariance,
reversibility round-tripping the frozen partitioned shape, the inverted scope
boundary, and fresh-bootstrap convergence.

Layers (the 0007 migration-test conventions, generalized from one parent to six):

  * **Target-safety guard, asserted positively and non-skippably.** The pure
    ``check_migration_target`` refusal logic is unit-testable without a live
    bind: refuses Customer Master outright, refuses any non-expected database,
    passes only the DIS database.
  * **The any-date proof (the load-bearing test of the slice).** Inserts dated
    WELL OUTSIDE any bootstrap partition window — far-future and pre-window —
    land in the event tables with no missing-partition error, via the APP role
    (so the grant and the RLS WITH CHECK are proven in the same write).
    Pre-0009 these nacked the batch (LOUD, per D77); they must now land.
    signal_history is proven with a pre-window PAST date only:
    ck_*_as_of_date_not_future forbids future dates by design.
  * **RLS tenant isolation identical through the drop-recreate.** Tenant A's
    row is invisible under tenant B's ``app.tenant_id`` and visible under
    tenant A's — proven via raw reads on one representative parent per schema.
  * **Plain shape at head, all six.** No partkey, zero pg_inherits children,
    PK (id), the event_date/as_of_date derivation CHECKs and signal_history
    natural keys present, FORCE RLS intact, app-role grants intact.
  * **Reversible cycle against the live DIS database (5433).** ``upgrade
    head`` leaves the six plain; ``downgrade 0008`` recreates the frozen
    partitioned forms (RANGE keys, composite PKs, 7 fresh CURRENT_DATE-
    relative children each); ``upgrade head`` returns to shapes identical to
    the first pass. Errors — never skips — when the stack is absent.
  * **Inverted scope boundary (this slice's hard limit).** The six ARE plain
    at head — and nothing else moved: audit.events stays plain (D77/0007
    untouched), the hot tables (store_sku_current_position, both schemas) and
    bronze stay plain and present.
  * **Fresh-bootstrap convergence on a scratch DB (the 9a lesson).** A scratch
    database stops at 0008 first: 0001's now-empty PARTITIONED manifest plus
    the plain DDL files must already build the plain shape (the manifest
    edit). 0009 on that manifest-fresh database is then shape-idempotent, and
    the fresh end state equals the delta-path database's, for all six.

See: decisions.md D77 (revised), D29/D34, hard rule 7.
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
from dis_core.ids import new_uuid7
from dis_rls import create_rls_engine

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0009_canonical_staging_departition.py"

_SCRATCH_DB = "ithina_dis_scratch_0009"

# The six de-partitioned parents: relation -> (pk constraint name, frozen
# partition key). The key is what `downgrade 0008` must restore verbatim.
_PARENTS: dict[str, tuple[str, str]] = {
    "canonical.store_sku_sale_events": ("pk_ssse", "RANGE (event_date)"),
    "canonical.store_sku_change_events": ("pk_ssce", "RANGE (event_date)"),
    "canonical.store_sku_signal_history": ("pk_sssh", "RANGE (as_of_date)"),
    "staging.store_sku_sale_events": ("pk_st_ssse", "RANGE (event_date)"),
    "staging.store_sku_change_events": ("pk_st_ssce", "RANGE (event_date)"),
    "staging.store_sku_signal_history": ("pk_st_sssh", "RANGE (as_of_date)"),
}

# Constraints whose PRESENCE is load-bearing for Slice 21's re-partition (the
# derivation CHECKs define the date columns' semantics) and for the daily
# grain (the signal_history natural keys keep as_of_date).
_KEPT_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "canonical.store_sku_sale_events": ("ck_ssse_event_date_matches_sale_timestamp",),
    "canonical.store_sku_change_events": ("ck_ssce_event_date_matches_source_ts",),
    "canonical.store_sku_signal_history": ("uq_sssh_natural",),
    "staging.store_sku_sale_events": ("ck_st_ssse_event_date_matches_sale_timestamp",),
    "staging.store_sku_change_events": ("ck_st_ssce_event_date_matches_source_ts",),
    "staging.store_sku_signal_history": ("uq_st_sssh_natural",),
}

# The inverted scope boundary: this slice converts the six ONLY. audit.events
# stays plain (D77/0007); the hot tables and bronze stay plain and present.
_MUST_STAY_PLAIN = (
    "audit.events",
    "canonical.store_sku_current_position",
    "staging.store_sku_current_position",
    "bronze.data_ingress_events",
)


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0009", _MIGRATION_PATH)
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
            "POSTGRES_ADMIN_URL is not set — the 0009 migration tests need the admin "
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
    """RLS app-role engine for the writer-level proofs (the 0007 test pattern)."""
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the 0009 any-date proof refuses to skip "
            "silently. Bring up the stack (make run-local) and export POSTGRES_URL "
            "(5433 / ithina_dis_db)."
        )
    from dis_testing.seed import seed_default_fixtures

    try:
        seed_default_fixtures(url=url)  # FK targets: tenants, stores, mapping; idempotent
    except Exception as exc:  # noqa: BLE001 — stack down → ERROR loudly, never skip
        raise StackRequiredError(
            f"DIS Postgres unreachable for the 0009 proofs ({exc!r}); refusing "
            "to skip. Bring up the stack (make run-local)."
        ) from exc

    eng = create_rls_engine(url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture(scope="module")
def mapping_version_id(admin_url: str, admin_engine: Engine) -> int:
    """The seeded default mapping's version id (FK target for event inserts)."""
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


def _pk_def(engine: Engine, relation: str, pk_name: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = CAST(:r AS regclass) AND conname = :n"
                ),
                {"r": relation, "n": pk_name},
            ).scalar_one()
        )


def _partition_children(engine: Engine, relation: str) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_inherits i ON c.oid = i.inhrelid "
                    "WHERE i.inhparent = CAST(:r AS regclass) ORDER BY 1"
                ),
                {"r": relation},
            ).scalars()
        )


def _app_role_privileges(engine: Engine, relation: str) -> set[str]:
    schema, table = relation.split(".")
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_schema = :s AND table_name = :t "
                    "AND grantee = 'ithina_dis_user'"
                ),
                {"s": schema, "t": table},
            ).all()
        }


def _shape(engine: Engine, relation: str) -> dict[str, object]:
    """The full normalized end-state shape of one parent, for cycle +
    convergence checks. Captures EVERY constraint and index dynamically
    (no name allowlist can drift stale)."""
    schema, table = relation.split(".")
    shape: dict[str, object] = {}
    with engine.connect() as conn:
        shape["partkey"] = conn.execute(
            text("SELECT pg_get_partkeydef(CAST(:r AS regclass))"), {"r": relation}
        ).scalar()
        shape["columns"] = [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, "
                    "COALESCE(character_maximum_length, -1), collation_name, "
                    "COALESCE(column_default, '') "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t "
                    "ORDER BY column_name"
                ),
                {"s": schema, "t": table},
            ).all()
        ]
        shape["constraints"] = {
            str(r[0]): str(r[1])
            for r in conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = CAST(:r AS regclass) ORDER BY conname"
                ),
                {"r": relation},
            ).all()
        }
        shape["indexes"] = {
            str(r[0]): str(r[1])
            for r in conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = :s AND tablename = :t ORDER BY indexname"
                ),
                {"s": schema, "t": table},
            ).all()
        }
        shape["triggers"] = sorted(
            str(r[0])
            for r in conn.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid = CAST(:r AS regclass) AND NOT tgisinternal"
                ),
                {"r": relation},
            ).all()
        )
        shape["rls"] = (
            conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:r AS regclass)"
                ),
                {"r": relation},
            )
            .one()
            ._asdict()
        )
        shape["policy"] = (
            conn.execute(
                text(
                    "SELECT permissive, roles, cmd, qual, with_check FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = :t "
                    "AND policyname = 'tenant_isolation'"
                ),
                {"s": schema, "t": table},
            )
            .one()
            ._asdict()
        )
    return shape


def _all_shapes(engine: Engine) -> dict[str, dict[str, object]]:
    return {relation: _shape(engine, relation) for relation in _PARENTS}


def _assert_plain_shape(engine: Engine) -> None:
    """The slice's acceptance shape, from live catalogs, for all six."""
    for relation, (pk_name, _frozen_key) in _PARENTS.items():
        assert _partkey(engine, relation) is None, f"{relation} is still partitioned"
        assert _partition_children(engine, relation) == [], f"{relation} still has child partitions"
        assert _pk_def(engine, relation, pk_name) == "PRIMARY KEY (id)", (
            f"{relation} PK is not (id) — the D77 PK precedent was not applied"
        )
        shape = _shape(engine, relation)
        constraints = shape["constraints"]
        assert isinstance(constraints, dict)
        for name in _KEPT_CONSTRAINTS[relation]:
            assert name in constraints, (
                f"constraint {name} missing on plain {relation} — a Slice 21 "
                f"re-partition invariant was dropped"
            )
        rls = shape["rls"]
        assert isinstance(rls, dict)
        assert rls == {"relrowsecurity": True, "relforcerowsecurity": True}
        # The app role can still write (the drop-recreate must not lose the grant).
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= _app_role_privileges(engine, relation)


# ---------------------------------------------------------------------------
# The any-date proof (the load-bearing test of the slice).
# ---------------------------------------------------------------------------

_FAR_FUTURE = datetime(2031, 1, 7, 12, 0, 0, tzinfo=UTC)
_PRE_WINDOW = datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC)


async def _insert_change_event(
    engine: AsyncEngine,
    *,
    schema: str,
    tenant_id: str,
    when: datetime,
    mapping_version_id: int,
    trace_id: str,
) -> None:
    """One change-event INSERT as the APP role under the tenant's RLS context.

    The D33/D38 first-class source identity columns exist on canonical only;
    the staging mirror still carries them inside ingest_metadata (its
    introspected live shape), so the column list branches per schema.
    """
    source_identity_cols = ", source_id, source_event_id" if schema == "canonical" else ""
    source_identity_vals = ", :source_id, :source_event_id" if schema == "canonical" else ""
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            await conn.execute(
                text(
                    f"INSERT INTO {schema}.store_sku_change_events "  # noqa: S608 — schema from _PARENTS
                    "(event_date, tenant_id, store_id, sku_id, event_category, event_subtype, "
                    f" source_event_timestamp, value_after{source_identity_cols}, "
                    " mapping_version_id, trace_id, dis_channel) "
                    "VALUES ((CAST(:ts AS timestamptz) AT TIME ZONE 'UTC')::date, "
                    " CAST(:tenant AS uuid), CAST(:store AS uuid), :sku, 'PRICE', "
                    " 'RETAIL_PRICE_CHANGE', CAST(:ts AS timestamptz), "
                    f" CAST(:value_after AS jsonb){source_identity_vals}, "
                    " :mapping_version_id, CAST(:trace AS uuid), 'csv_upload')"
                ),
                {
                    "ts": when.isoformat(),
                    "tenant": tenant_id,
                    "store": str(fx.PRIMARY_STORE.uuid),
                    "sku": "MIG0009-SKU",
                    "value_after": '{"price": "9.99"}',
                    "mapping_version_id": mapping_version_id,
                    "trace": trace_id,
                    **(
                        {"source_id": fx.DEFAULT_SOURCE_ID, "source_event_id": f"mig0009:{trace_id}"}
                        if schema == "canonical"
                        else {}
                    ),
                },
            )


async def _read_back_change_event(engine: AsyncEngine, *, schema: str, tenant_id: str, trace_id: str) -> int:
    """Raw COUNT with a manual set_config — independent of the insert path."""
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            count = (
                await conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {schema}.store_sku_change_events "  # noqa: S608
                        "WHERE trace_id = CAST(:trace AS uuid)"
                    ),
                    {"trace": trace_id},
                )
            ).scalar_one()
            return int(count)


def _cleanup_by_trace(admin_engine: Engine, relation: str, trace_id: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text(f"DELETE FROM {relation} WHERE trace_id = CAST(:trace AS uuid)"),  # noqa: S608
            {"trace": trace_id},
        )


async def test_any_date_lands_change_events_both_schemas(
    app_engine: AsyncEngine, admin_engine: Engine, mapping_version_id: int
) -> None:
    """Far-future AND pre-window event dates land in canonical and staging
    change events — no partition window exists to miss. Pre-0009 either date
    raised "no partition of relation" and nacked the batch (D77 Scope)."""
    _alembic("upgrade", "head")
    tenant = str(fx.PRIMARY_TENANT.uuid)
    cases = [
        ("canonical", _FAR_FUTURE),
        ("canonical", _PRE_WINDOW),
        ("staging", _FAR_FUTURE),
    ]
    traces = [str(new_uuid7()) for _ in cases]
    try:
        for (schema, when), trace in zip(cases, traces, strict=True):
            await _insert_change_event(
                app_engine,
                schema=schema,
                tenant_id=tenant,
                when=when,
                mapping_version_id=mapping_version_id,
                trace_id=trace,
            )
            assert (
                await _read_back_change_event(app_engine, schema=schema, tenant_id=tenant, trace_id=trace)
                == 1
            ), f"{schema} change event dated {when.date()} did not land"
    finally:
        for (schema, _when), trace in zip(cases, traces, strict=True):
            _cleanup_by_trace(admin_engine, f"{schema}.store_sku_change_events", trace)


async def test_any_date_lands_sale_events(
    app_engine: AsyncEngine, admin_engine: Engine, mapping_version_id: int
) -> None:
    """A far-future sale event lands in canonical.store_sku_sale_events."""
    _alembic("upgrade", "head")
    tenant = str(fx.PRIMARY_TENANT.uuid)
    trace = str(new_uuid7())
    try:
        async with app_engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
                await conn.execute(
                    text(
                        "INSERT INTO canonical.store_sku_sale_events "
                        "(event_date, tenant_id, store_id, sku_id, event_subtype, "
                        " source_sale_timestamp, quantity, unit_retail_price, unit_sale_price, "
                        " tax_treatment, currency, source_id, source_event_id, "
                        " mapping_version_id, trace_id, dis_channel) "
                        "VALUES ((CAST(:ts AS timestamptz) AT TIME ZONE 'UTC')::date, "
                        " CAST(:tenant AS uuid), CAST(:store AS uuid), :sku, 'SALE', "
                        " CAST(:ts AS timestamptz), 1, 9.99, 9.99, "
                        " 'INCLUSIVE', 'INR', :source_id, :source_event_id, "
                        " :mapping_version_id, CAST(:trace AS uuid), 'csv_upload')"
                    ),
                    {
                        "ts": _FAR_FUTURE.isoformat(),
                        "tenant": tenant,
                        "store": str(fx.PRIMARY_STORE.uuid),
                        "sku": "MIG0009-SKU",
                        "source_id": fx.DEFAULT_SOURCE_ID,
                        "source_event_id": f"mig0009:{trace}",
                        "mapping_version_id": mapping_version_id,
                        "trace": trace,
                    },
                )
        with admin_engine.connect() as conn:
            landed = conn.execute(
                text(
                    "SELECT COUNT(*) FROM canonical.store_sku_sale_events "
                    "WHERE trace_id = CAST(:trace AS uuid)"
                ),
                {"trace": trace},
            ).scalar_one()
        assert landed == 1, "far-future sale event did not land"
    finally:
        _cleanup_by_trace(admin_engine, "canonical.store_sku_sale_events", trace)


async def test_pre_window_date_lands_signal_history(app_engine: AsyncEngine, admin_engine: Engine) -> None:
    """A pre-window as_of_date lands in canonical.store_sku_signal_history.
    (Future dates stay forbidden BY DESIGN: ck_sssh_as_of_date_not_future is
    semantics, not partition routing.)"""
    _alembic("upgrade", "head")
    tenant = str(fx.PRIMARY_TENANT.uuid)
    trace = str(new_uuid7())
    try:
        async with app_engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
                await conn.execute(
                    text(
                        "INSERT INTO canonical.store_sku_signal_history "
                        "(as_of_date, tenant_id, store_id, sku_id, trace_id) "
                        "VALUES (CAST(:as_of AS date), CAST(:tenant AS uuid), "
                        " CAST(:store AS uuid), :sku, CAST(:trace AS uuid))"
                    ),
                    {
                        "as_of": _PRE_WINDOW.date().isoformat(),
                        "tenant": tenant,
                        "store": str(fx.PRIMARY_STORE.uuid),
                        "sku": "MIG0009-SKU",
                        "trace": trace,
                    },
                )
        with admin_engine.connect() as conn:
            landed = conn.execute(
                text(
                    "SELECT COUNT(*) FROM canonical.store_sku_signal_history "
                    "WHERE trace_id = CAST(:trace AS uuid)"
                ),
                {"trace": trace},
            ).scalar_one()
        assert landed == 1, "pre-window signal_history row did not land"
    finally:
        _cleanup_by_trace(admin_engine, "canonical.store_sku_signal_history", trace)


# ---------------------------------------------------------------------------
# RLS tenant isolation survives the drop-recreate.
# ---------------------------------------------------------------------------


async def test_rls_isolation_survives_departition(
    app_engine: AsyncEngine, admin_engine: Engine, mapping_version_id: int
) -> None:
    """One representative parent per schema: tenant A's row is invisible under
    tenant B's app.tenant_id and visible under tenant A's."""
    _alembic("upgrade", "head")
    tenant_a = str(fx.PRIMARY_TENANT.uuid)
    tenant_b = str(fx.TENANTS[1].uuid)
    traces = {schema: str(new_uuid7()) for schema in ("canonical", "staging")}
    try:
        for schema, trace in traces.items():
            await _insert_change_event(
                app_engine,
                schema=schema,
                tenant_id=tenant_a,
                when=_FAR_FUTURE,
                mapping_version_id=mapping_version_id,
                trace_id=trace,
            )
            assert (
                await _read_back_change_event(app_engine, schema=schema, tenant_id=tenant_b, trace_id=trace)
            ) == 0, f"{schema}: tenant B can see tenant A's row — RLS lost in the drop-recreate"
            assert (
                await _read_back_change_event(app_engine, schema=schema, tenant_id=tenant_a, trace_id=trace)
            ) == 1, f"{schema}: tenant A cannot see its own row"
    finally:
        for schema, trace in traces.items():
            _cleanup_by_trace(admin_engine, f"{schema}.store_sku_change_events", trace)


# ---------------------------------------------------------------------------
# Plain shape at head + the inverted scope boundary.
# ---------------------------------------------------------------------------


def test_all_six_plain_at_head(admin_engine: Engine) -> None:
    _alembic("upgrade", "head")
    _assert_plain_shape(admin_engine)


def test_scope_boundary_nothing_else_moved(admin_engine: Engine) -> None:
    """This slice converts the six ONLY. audit.events stays plain (D77/0007
    untouched); the hot tables and bronze stay plain and present."""
    _alembic("upgrade", "head")
    for relation in _MUST_STAY_PLAIN:
        assert _partkey(admin_engine, relation) is None, (
            f"{relation} became partitioned — migration 0009 must touch the six parents only"
        )
        assert _partition_children(admin_engine, relation) == []


# ---------------------------------------------------------------------------
# Reversible cycle round-tripping the frozen partitioned shape.
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="downgrade-reversibility deferred until staging (D99)")
def test_migration_cycle_departition_and_back(admin_engine: Engine) -> None:
    # apply-to-head stays covered by test_all_six_plain_at_head / test_scope_boundary_nothing_else_moved
    # + test_fresh_bootstrap_converges_with_delta_path. The downgrade-to-0008 leg traverses 0010's
    # view-dropping downgrade and left config.source_mappings_v comment-less (D99 gap; case 3).
    # upgrade head first (idempotent if already at 0009): the plain shapes.
    _alembic("upgrade", "head")
    _assert_plain_shape(admin_engine)
    plain_shapes = _all_shapes(admin_engine)

    # downgrade to 0008: the frozen partitioned forms return with FRESH
    # CURRENT_DATE-relative 7-day windows (not the original 2026-06 dates).
    _alembic("downgrade", "0008")
    for relation, (pk_name, frozen_key) in _PARENTS.items():
        assert _partkey(admin_engine, relation) == frozen_key, (
            f"{relation}: downgrade did not restore the frozen partition key"
        )
        pk = _pk_def(admin_engine, relation, pk_name)
        key_column = frozen_key.removeprefix("RANGE (").removesuffix(")")
        assert pk == f"PRIMARY KEY (id, {key_column})", (
            f"{relation}: downgrade did not restore the composite PK ({pk})"
        )
        children = _partition_children(admin_engine, relation)
        table = relation.split(".")[1]
        assert len(children) == 7, f"{relation}: downgrade created {len(children)} partitions, expected 7"
        assert all(c.startswith(f"{table}_p") for c in children)
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= _app_role_privileges(admin_engine, relation)

    # re-upgrade: the plain shapes again, identical to the first pass.
    _alembic("upgrade", "head")
    _assert_plain_shape(admin_engine)
    assert _all_shapes(admin_engine) == plain_shapes


# ---------------------------------------------------------------------------
# Fresh-bootstrap convergence on a scratch DB (the 9a lesson).
# ---------------------------------------------------------------------------


def test_fresh_bootstrap_converges_with_delta_path(admin_url: str, admin_engine: Engine) -> None:
    """The fresh path (0001 applies the now-plain manifest with its EMPTY
    PARTITIONED list; 0009 re-applies the same files) must land the IDENTICAL
    six shapes the delta path leaves behind (the delta path being the
    partitioned parents converted by 0009)."""
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
        # Stop at 0008 first: 0001 has applied the manifest verbatim (empty
        # PARTITIONED list + plain DDL files), so this IS the manifest-built
        # (already-plain) shape, before 0009 runs.
        _alembic("upgrade", "0008", env_overrides=scratch_env)
        manifest_shapes = _all_shapes(scratch_engine)
        for relation, shape in manifest_shapes.items():
            assert shape["partkey"] is None, (
                f"fresh bootstrap built a PARTITIONED {relation} — the 0001 manifest "
                "still partitions it (a PARTITIONED-list tuple is back?)"
            )

        # 0009 on a manifest-fresh database: drop-and-recreate from the SAME
        # files — shape-identical, so manifest-as-source-of-truth holds.
        _alembic("upgrade", "head", env_overrides=scratch_env)
        with scratch_engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert head == _alembic_head()
        assert _all_shapes(scratch_engine) == manifest_shapes, (
            "migration 0009 CHANGED a manifest-fresh database — the manifest "
            "no longer carries its end state (drift self-healed)"
        )

        # And the fresh end state equals the delta-path end state.
        assert _all_shapes(scratch_engine) == _all_shapes(admin_engine)
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
