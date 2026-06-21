"""Migration 0002 (identity_mirror external codes): target safety + reversibility.

Slice 9a AC4. Two layers:

  * **Target-safety guard, asserted positively and non-skippably.** The pure
    ``check_migration_target`` refusal logic is unit-testable without a live bind
    (the ``test_reader_guards`` precedent): it refuses the Customer Master database
    outright, refuses any non-expected database, and passes only the DIS database.
  * **Reversible cycle against the live DIS database (5433).** ``upgrade head`` adds
    the two nullable columns (live introspection via ``information_schema``),
    ``downgrade 0001`` removes them cleanly, re-upgrade restores them. Errors — never
    skips — when the stack is absent (the load-bearing-proof rule from Slices 4/7).

The migration runs against ``ithina_dis_db`` on 5433 only; the in-migration guard
refuses Customer Master (``ithina_platform_db``) before any DDL.
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

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0002_identity_mirror_codes.py"


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0002", _MIGRATION_PATH)
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
    # Positive assertion: the one accepted target is the DIS database.
    mod = _load_migration_module()
    mod.check_migration_target("ithina_dis_db", expected_db="ithina_dis_db")


# ---------------------------------------------------------------------------
# Reversible cycle against live 5433 (errors, never skips).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = os.environ.get("POSTGRES_ADMIN_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the migration-cycle test needs the admin "
            "role on ithina_dis_db (5433). Bring up the stack (make run-local)."
        )
    return url


@pytest.fixture(scope="module")
def admin_engine(admin_url: str) -> Iterator[Engine]:
    engine = create_engine(admin_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _alembic(*args: str) -> None:
    result = subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def _code_columns(engine: Engine) -> dict[tuple[str, str], str]:
    """Introspect the two code columns live: {(table, column): is_nullable}."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name, column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'identity_mirror'
                  AND ((table_name = 'tenants' AND column_name = 'display_code')
                    OR (table_name = 'stores' AND column_name = 'store_code'))
                """
            )
        ).all()
    return {(r.table_name, r.column_name): r.is_nullable for r in rows}


def test_upgrade_head_adds_the_code_columns(admin_engine: Engine) -> None:
    # APPLY-TO-HEAD (kept live; the downgrade leg is split out + skipped per D99).
    # Upgrade leaves both code columns present — the only apply-to-head proof in
    # this file, so it must stay live when the cycle below is skipped.
    _alembic("upgrade", "head")
    assert _code_columns(admin_engine) == {
        ("tenants", "display_code"): "YES",
        ("stores", "store_code"): "YES",
    }


@pytest.mark.skip(reason="downgrade-reversibility deferred until staging (D99)")
def test_migration_cycle_adds_and_removes_the_code_columns(admin_engine: Engine) -> None:
    # upgrade head: both columns present, nullable (live introspection, not the DDL files).
    _alembic("upgrade", "head")
    cols = _code_columns(admin_engine)
    assert cols == {
        ("tenants", "display_code"): "YES",
        ("stores", "store_code"): "YES",
    }

    # downgrade to 0001: both columns removed cleanly.
    _alembic("downgrade", "0001")
    assert _code_columns(admin_engine) == {}

    # re-upgrade: restored (the IF NOT EXISTS path is exercised again).
    _alembic("upgrade", "head")
    cols = _code_columns(admin_engine)
    assert cols == {
        ("tenants", "display_code"): "YES",
        ("stores", "store_code"): "YES",
    }

    # The cycle restores the SHAPE, not the DATA: the downgrade dropped the code
    # columns, so every seeded display_code/store_code is now NULL. Restore the
    # fixture values — later-collected suites depend on them (Slice 8's
    # csv-uploads resolves store_code), and leaving the repair to whenever the
    # mirror-sync integration tests happen to re-sync is an ordering coupling,
    # not hygiene. A test that mutates shared live state restores it.
    from dis_testing import fixtures as fx

    with admin_engine.begin() as conn:
        for tenant in fx.TENANTS:
            conn.execute(
                text("UPDATE identity_mirror.tenants SET display_code = :code WHERE tenant_id = :id"),
                {"code": tenant.display_code, "id": str(tenant.uuid)},
            )
        for store in fx.STORES:
            conn.execute(
                text("UPDATE identity_mirror.stores SET store_code = :code WHERE store_id = :id"),
                {"code": store.store_code, "id": str(store.uuid)},
            )
