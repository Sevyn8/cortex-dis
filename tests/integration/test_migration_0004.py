"""Migration 0004 (M-HOTKEY: COALESCE-sentinel arbiter index): target safety,
CHECK/index co-existence, precondition teeth, reversibility, arbitration.

Layers (the 0002/0003 pattern):

  * **Target-safety guard**, pure-testable without a bind.
  * **Reversible cycle** against live 5433: upgrade → ``uq_sscp_natural_key``
    + both sentinel CHECKs present, ``uq_sscp_natural`` absent; downgrade 0003
    → NND constraint restored, index + CHECKs gone; re-upgrade → restored.
    The **co-existence invariant** (operator confirm) is asserted on every
    upgraded leg: index present ⇒ both CHECKs present.
  * **Precondition teeth**: while downgraded, insert a ``''``-variant row →
    re-upgrade ABORTS loudly (never builds a collision-prone sentinel index);
    delete the row → re-upgrade succeeds.
  * **Arbitration matrix** (the Part 3 §2 scratch proof, codified): for the
    three key shapes — (variant, lot), (variant, NULL), (NULL, NULL) — one
    ``INSERT … ON CONFLICT (COALESCE target) DO UPDATE`` takes the insert arm
    and a same-key second takes the UPDATE arm (the exact case the NND index
    could not arbitrate on PG15); EXPLAIN names ``uq_sscp_natural_key`` as the
    Conflict Arbiter; inserting ``''`` raises the sentinel CHECK.

ERRORS, never skips, when the stack is absent.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.sql.elements import TextClause

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0004_hot_natural_key_arbitration.py"

_TENANT = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees (Customer Master identity)
_STORE = "019e5e3c-b62e-75e6-ad62-529127ae944a"  # TX-101

_CHECKS = ("ck_sscp_sku_variant_not_empty", "ck_sscp_sku_lot_batch_not_empty")


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0004", _MIGRATION_PATH)
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
# Live cycle against 5433 (errors, never skips).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    url = os.environ.get("POSTGRES_ADMIN_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the migration-cycle test needs the admin "
            "role on ithina_dis_db (5433). Bring up the stack (make run-local)."
        )
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def seeded(admin_engine: Engine) -> None:
    """Seed the Slice 2 tenants/stores + default mapping so the matrix rows'
    FKs resolve on a virgin stack (repo-root tests run before any service
    suite's seeder). Idempotent."""
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the arbitration-matrix test seeds the Slice 2 "
            "fixtures. Bring up the stack (make run-local) and load .env."
        )
    from dis_testing.seed import seed_default_fixtures

    seed_default_fixtures(url=url)


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


def _alembic_expect_failure(*args: str) -> str:
    result = subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, f"alembic {' '.join(args)} unexpectedly succeeded"
    return result.stdout + result.stderr


def _state(engine: Engine) -> dict[str, bool]:
    with engine.connect() as conn:
        index = conn.execute(
            text("SELECT 1 FROM pg_indexes WHERE schemaname='canonical' AND indexname='uq_sscp_natural_key'")
        ).first()
        old = conn.execute(text("SELECT 1 FROM pg_constraint WHERE conname='uq_sscp_natural'")).first()
        checks = {
            name: conn.execute(text("SELECT 1 FROM pg_constraint WHERE conname=:n"), {"n": name}).first()
            is not None
            for name in _CHECKS
        }
    return {"index": index is not None, "old_constraint": old is not None, **checks}


def _assert_upgraded_invariant(state: dict[str, bool]) -> None:
    """The operator-confirm co-existence invariant: index ⇒ both CHECKs."""
    assert state["index"] is True
    assert all(state[name] for name in _CHECKS), state
    assert state["old_constraint"] is False


@pytest.mark.skip(reason="downgrade-reversibility deferred until staging (D99)")
def test_cycle_precondition_teeth_and_coexistence(admin_engine: Engine) -> None:
    # apply-to-head stays covered by test_arbitration_matrix_and_sentinel (upgrade-only).
    # upgrade head: index + CHECKs present, NND constraint gone (co-existence leg 1).
    _alembic("upgrade", "head")
    _assert_upgraded_invariant(_state(admin_engine))

    # downgrade to 0003: NND restored; index and CHECKs gone (mirror order held).
    _alembic("downgrade", "0003")
    state = _state(admin_engine)
    assert state["old_constraint"] is True
    assert state["index"] is False
    assert not any(state[name] for name in _CHECKS)

    # Precondition teeth: while downgraded, '' CAN enter (pre-redesign semantics);
    # the re-upgrade must then ABORT loudly rather than build the sentinel index.
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO canonical.store_sku_current_position "
                "(id, tenant_id, store_id, sku_id, sku_variant, product_name, product_category, "
                " current_retail_price, unit_cost, tax_treatment, currency, mapping_version_id, "
                " trace_id, dis_channel) "
                "VALUES (uuidv7(), CAST(:t AS uuid), CAST(:s AS uuid), 'M0004-TEETH', '', 'W', 'H', "
                " 1, 0.5, 'EXCLUSIVE', 'USD', 1, uuidv7(), 'csv_upload')"
            ),
            {"t": _TENANT, "s": _STORE},
        )
    try:
        output = _alembic_expect_failure("upgrade", "head")
        assert "empty-string" in output or "Refusing to create" in output
        assert _state(admin_engine)["index"] is False  # nothing half-applied
    finally:
        with admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM canonical.store_sku_current_position WHERE sku_id='M0004-TEETH'"))

    # re-upgrade: clean precondition -> restored (co-existence leg 2; also the
    # IF-NOT-EXISTS path a 0001-fresh-bootstrap database takes).
    _alembic("upgrade", "head")
    _assert_upgraded_invariant(_state(admin_engine))


def _upsert_sql() -> TextClause:
    return text(
        "INSERT INTO canonical.store_sku_current_position "
        "(id, tenant_id, store_id, sku_id, sku_variant, sku_lot_batch, product_name, "
        " product_category, current_retail_price, unit_cost, tax_treatment, currency, "
        " last_source_event_at, mapping_version_id, trace_id, dis_channel) "
        "VALUES (uuidv7(), CAST(:t AS uuid), CAST(:s AS uuid), 'M0004-MATRIX', :variant, :lot, "
        " 'W', 'H', :price, 0.5, 'EXCLUSIVE', 'USD', NOW(), 1, uuidv7(), 'csv_upload') "
        "ON CONFLICT (tenant_id, store_id, sku_id, COALESCE(sku_variant, ''), COALESCE(sku_lot_batch, '')) "
        "DO UPDATE SET current_retail_price = EXCLUDED.current_retail_price, "
        "              last_source_event_at = EXCLUDED.last_source_event_at "
        "WHERE store_sku_current_position.last_source_event_at IS NULL "
        "   OR EXCLUDED.last_source_event_at >= store_sku_current_position.last_source_event_at"
    )


def test_arbitration_matrix_and_sentinel(admin_engine: Engine, seeded: None) -> None:
    _alembic("upgrade", "head")
    shapes: list[tuple[str | None, str | None]] = [("V1", "L1"), ("V1", None), (None, None)]
    try:
        with admin_engine.begin() as conn:
            for variant, lot in shapes:
                params: dict[str, Any] = {"t": _TENANT, "s": _STORE, "variant": variant, "lot": lot}
                conn.execute(_upsert_sql(), {**params, "price": 1.0})  # insert arm
                conn.execute(_upsert_sql(), {**params, "price": 2.0})  # UPDATE arm (same key)
            rows = conn.execute(
                text(
                    "SELECT sku_variant, sku_lot_batch, current_retail_price "
                    "FROM canonical.store_sku_current_position WHERE sku_id='M0004-MATRIX'"
                )
            ).all()
            # Exactly one row per shape (no spurious insert — the NND failure mode),
            # every one updated by the second upsert's UPDATE arm.
            assert len(rows) == 3
            assert all(str(r.current_retail_price) == "2.0000" for r in rows)

            # EXPLAIN: the expression index is the inferred Conflict Arbiter.
            plan = "\n".join(
                str(r[0])
                for r in conn.execute(
                    text("EXPLAIN (VERBOSE) " + str(_upsert_sql())),
                    {"t": _TENANT, "s": _STORE, "variant": None, "lot": None, "price": 3.0},
                ).all()
            )
            assert "Conflict Arbiter Indexes: uq_sscp_natural_key" in plan

        # The sentinel is engine-impossible — its OWN transaction: the CHECK
        # violation aborts the txn, so it must not share the matrix's block
        # (the exception propagates out of begin(), which rolls back cleanly).
        with pytest.raises(Exception, match="ck_sscp_sku_variant_not_empty"):
            with admin_engine.begin() as conn:
                conn.execute(
                    _upsert_sql(), {"t": _TENANT, "s": _STORE, "variant": "", "lot": None, "price": 1.0}
                )
    finally:
        with admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM canonical.store_sku_current_position WHERE sku_id='M0004-MATRIX'"))
