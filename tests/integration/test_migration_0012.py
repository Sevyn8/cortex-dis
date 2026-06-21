"""Migration 0012 (nullable hot columns: unit_cost, product_category — Slice 16j).

Proves, against the live DIS database (5433 / ithina_dis_db) and a scratch DB:

  * **Target-safety guard** (pure, always-run, never skips): refuses Customer Master
    and any non-DIS database; passes the DIS database.
  * **Nullable at head** (the headline schema effect): after upgrade-to-head, both
    canonical.store_sku_current_position.unit_cost and .product_category are NULLABLE,
    and ck_sscp_unit_cost_non_negative is RETAINED (NULL-safe, not dropped).
  * **Fresh == migrated** on a scratch DB: 0001 applies the edited DDL files (already
    nullable), so a fresh bootstrap to head lands the identical nullability the delta
    path leaves.

Downgrade-reversibility (the SET NOT NULL round-trip) is deferred until staging (D99):
the downgrade leg is authored in the migration, but its round-trip test is skipped with
the shared, greppable reason.

See: docs/slices/slice-16j-nullable-canonical-columns.md, decisions.md D99 (downgrade
defer), the 0011 fresh==migrated precedent.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0012_nullable_hot_unit_cost_product_category.py"
_SCRATCH_DB = "ithina_dis_scratch_0012"

_SCHEMA = "canonical"
_TABLE = "store_sku_current_position"
_TARGET_COLUMNS = ("unit_cost", "product_category")
_UNIT_COST_CHECK = "ck_sscp_unit_cost_non_negative"


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0012", _MIGRATION_PATH)
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
            "POSTGRES_ADMIN_URL is not set — the 0012 migration tests need the admin "
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


# --- Introspection helpers ----------------------------------------------------


def _nullability(engine: Engine, columns: tuple[str, ...]) -> dict[str, str]:
    """Map each target column to its information_schema is_nullable ('YES'/'NO')."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = ANY(:cols)"
            ),
            {"s": _SCHEMA, "t": _TABLE, "cols": list(columns)},
        ).all()
    return {row.column_name: row.is_nullable for row in rows}


def _check_present(engine: Engine, conname: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                    f"WHERE conrelid = '{_SCHEMA}.{_TABLE}'::regclass "  # noqa: S608 — fixed identifiers
                    "AND contype = 'c' AND conname = :c)"
                ),
                {"c": conname},
            ).scalar_one()
        )


# --- Nullable-at-head + CHECK retained ----------------------------------------


def test_both_columns_nullable_at_head_and_check_retained(admin_engine: Engine) -> None:
    """The headline schema effect: both targets NULLABLE at head, unit_cost CHECK kept."""
    _alembic("upgrade", "head")
    nullability = _nullability(admin_engine, _TARGET_COLUMNS)
    assert nullability == {"unit_cost": "YES", "product_category": "YES"}, (
        f"expected both targets nullable at head, got {nullability}"
    )
    assert _check_present(admin_engine, _UNIT_COST_CHECK), (
        f"{_UNIT_COST_CHECK} was dropped — it is NULL-safe and must be retained (Slice 16j)"
    )


# --- Fresh == migrated --------------------------------------------------------


def test_fresh_bootstrap_converges_with_delta_path(admin_url: str, admin_engine: Engine) -> None:
    """The fresh path (0001 applies the edited nullable DDL, then 0012's DROP NOT NULL is
    a no-op) lands the IDENTICAL nullability the delta path leaves at head — and the
    unit_cost CHECK is present on both paths (fresh == migrated)."""
    _alembic("upgrade", "head")
    delta = _nullability(admin_engine, _TARGET_COLUMNS)
    assert delta == {"unit_cost": "YES", "product_category": "YES"}

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
        assert head == "0012"
        fresh = _nullability(scratch_engine, _TARGET_COLUMNS)
        assert fresh == delta, (
            "fresh bootstrap produced different nullability than the migrated path — "
            "the DDL files and migration 0012 disagree (fresh != migrated)"
        )
        assert _check_present(scratch_engine, _UNIT_COST_CHECK), (
            "fresh bootstrap is missing the unit_cost CHECK present on the migrated path"
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


# --- Downgrade round-trip: deferred until staging (D99) -----------------------


@pytest.mark.skip(reason="downgrade-reversibility deferred until staging (D99)")
def test_downgrade_restores_not_null_then_reupgrade(admin_engine: Engine) -> None:
    """Round-trip: head (nullable) -> downgrade 0011 (SET NOT NULL) -> head (nullable).
    Skipped under D99; the downgrade leg is authored in the migration."""
    _alembic("upgrade", "head")
    _alembic("downgrade", "0011")
    restored = _nullability(admin_engine, _TARGET_COLUMNS)
    assert restored == {"unit_cost": "NO", "product_category": "NO"}
    _alembic("upgrade", "head")
    assert _nullability(admin_engine, _TARGET_COLUMNS) == {"unit_cost": "YES", "product_category": "YES"}
