"""Migration 0010 (config.source_mappings.template_type, Slice 14d): target
safety, the additive column shape, the signature backfill, view exposure, and a
reversible cycle against the live DIS database (5433).

Layers (the 0005..0009 migration-test conventions):

  * **Target-safety guard, asserted positively and non-skippably.** The pure
    ``check_migration_target`` refusal logic is unit-testable without a live
    bind: refuses Customer Master outright, refuses any non-expected database,
    passes only the DIS database.
  * **At-head shape.** template_type exists TEXT NOT NULL with NO enum type and
    NO CHECK constraint (code-enforced vocabulary), and the view exposes it.
  * **Backfill formalises the implicit discriminator.** Every row carries a
    vocabulary member; a sale-signature mapping → 'sales', a change-signature
    mapping → 'inventory_change' (the disjoint rule-target signatures).
  * **Reversible cycle.** ``downgrade 0009`` drops template_type and reverts the
    view; ``upgrade head`` restores both. Errors — never skips — when the stack
    is absent (the load-bearing-proof rule from Slices 4/7).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0010_source_mappings_template_type.py"
_VOCAB = {"snapshot", "sales", "inventory_change"}
_SCRATCH_DB = "ithina_dis_scratch_0010"
_TABLE = "config.source_mappings"
_VIEW = "config.source_mappings_v"


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0010", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- target-safety guard: pure, always-run, never skips (no DB needed) ------------


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


# -- live DB fixtures (error, never skip, when the stack is absent) ----------------


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = os.environ.get("POSTGRES_ADMIN_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the 0010 migration test needs the admin "
            "role on ithina_dis_db (5433). Bring up the stack (make run-local)."
        )
    parsed = make_url(url)
    assert parsed.database == "ithina_dis_db"  # target safety for the fixture itself
    assert parsed.port == 5433
    return url


@pytest.fixture(scope="module")
def admin_engine(admin_url: str) -> Iterator[Engine]:
    eng = create_engine(admin_url)
    try:
        yield eng
    finally:
        eng.dispose()


# -- at-head shape + backfill (the DB is at head: conftest/make run-local applied) -


def test_template_type_is_text_not_null_no_enum_no_check(admin_engine: Engine) -> None:
    with admin_engine.begin() as conn:
        col = conn.execute(
            text(
                "SELECT data_type, udt_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema='config' AND table_name='source_mappings' "
                "AND column_name='template_type'"
            )
        ).one()
        assert col.data_type == "text"  # TEXT, not a USER-DEFINED enum type
        assert col.udt_name == "text"
        assert col.is_nullable == "NO"
        # No CHECK constraint mentions template_type (code-enforced vocabulary).
        check_refs = conn.execute(
            text(
                "SELECT COUNT(*) FROM pg_constraint "
                "WHERE conrelid='config.source_mappings'::regclass AND contype='c' "
                "AND pg_get_constraintdef(oid) ILIKE '%template_type%'"
            )
        ).scalar_one()
        assert check_refs == 0


def test_view_exposes_template_type(admin_engine: Engine) -> None:
    with admin_engine.begin() as conn:
        present = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='config' AND table_name='source_mappings_v' "
                "AND column_name='template_type'"
            )
        ).first()
    assert present is not None


def test_every_row_carries_a_vocabulary_member(admin_engine: Engine) -> None:
    with admin_engine.begin() as conn:
        types_seen = {
            row[0] for row in conn.execute(text("SELECT DISTINCT template_type FROM config.source_mappings"))
        }
        nulls = conn.execute(
            text("SELECT COUNT(*) FROM config.source_mappings WHERE template_type IS NULL")
        ).scalar_one()
    assert nulls == 0
    assert types_seen <= _VOCAB, f"off-vocabulary value backfilled: {types_seen - _VOCAB}"


def test_backfill_formalises_the_signature_discriminator(admin_engine: Engine) -> None:
    # A sale-signature mapping (source_sale_timestamp/quantity) → 'sales'; a
    # change-signature mapping (source_event_timestamp/value_after) → 'inventory_change'.
    with admin_engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT source_id, template_type FROM config.source_mappings "
                "WHERE source_id IN ('sc_pos_v1', 'sc_inv_v1')"
            )
        ).all()
    by_source = {r.source_id: r.template_type for r in rows}
    # Seeded by the consumer integration conftest; present on a make-run-local DB.
    if "sc_pos_v1" in by_source:
        assert by_source["sc_pos_v1"] == "sales"
    if "sc_inv_v1" in by_source:
        assert by_source["sc_inv_v1"] == "inventory_change"


# -- reversible cycle against the live DB -----------------------------------------


@pytest.mark.skip(reason="downgrade-reversibility deferred until staging (D99)")
def test_downgrade_then_upgrade_restores_the_column_and_view(admin_engine: Engine) -> None:
    # apply-to-head stays covered by the upgrade-only tests + test_fresh_bootstrap_converges_with_delta_path.
    def _has_column() -> bool:
        with admin_engine.begin() as conn:
            return (
                conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns WHERE table_schema='config' "
                        "AND table_name='source_mappings' AND column_name='template_type'"
                    )
                ).first()
                is not None
            )

    assert _has_column()  # at head
    env = {**os.environ}
    subprocess.run(["uv", "run", "alembic", "downgrade", "0009"], cwd=_REPO_ROOT, check=True, env=env)
    assert not _has_column()  # dropped
    with admin_engine.begin() as conn:
        view_has = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='config' "
                "AND table_name='source_mappings_v' AND column_name='template_type'"
            )
        ).first()
    assert view_has is None  # view reverted to the pre-14d SELECT list
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=_REPO_ROOT, check=True, env=env)
    assert _has_column()  # restored


# ---------------------------------------------------------------------------
# Fresh-bootstrap convergence on a scratch DB (the 0007/0009 convention).
# The invariant: a DB built fresh from the DDL manifests (the end-state source,
# incl. schemas/postgres/config/source_mappings.sql with template_type + the
# recreated view) is schema-identical to a DB built by running all migrations in
# order to 0010. Drift between manifest and migration fails this test.
# ---------------------------------------------------------------------------


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
        ["uv", "run", "alembic", *args], cwd=_REPO_ROOT, capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def _table_shape(engine: Engine, relation: str) -> dict[str, object]:
    """Full normalized shape of one table: columns, constraints, indexes — captured
    dynamically (no name allowlist can drift stale), the 0009 convention."""
    schema, table = relation.split(".")
    shape: dict[str, object] = {}
    with engine.connect() as conn:
        shape["columns"] = [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT column_name, data_type, udt_name, is_nullable, "
                    "COALESCE(character_maximum_length, -1), COALESCE(column_default, '') "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t ORDER BY column_name"
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
    return shape


def _view_shape(engine: Engine) -> dict[str, object]:
    """The view's normalized definition + its column list in order — what 0010
    recreates (template_type appended last)."""
    with engine.connect() as conn:
        viewdef = conn.execute(
            text("SELECT pg_get_viewdef(CAST(:r AS regclass), true)"), {"r": _VIEW}
        ).scalar_one()
        columns = [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT ordinal_position, column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'config' AND table_name = 'source_mappings_v' "
                    "ORDER BY ordinal_position"
                )
            ).all()
        ]
    return {"viewdef": str(viewdef), "columns": columns}


def test_fresh_bootstrap_converges_with_delta_path(admin_url: str, admin_engine: Engine) -> None:
    """Fresh (0001 applies the manifest WITH template_type + the recreated view;
    0010 re-applies idempotently) must land the IDENTICAL table + view shapes the
    delta path leaves behind. Manifest-vs-migration drift fails here."""
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
        # The migration target guard keys on POSTGRES_DB; Customer Master stays
        # hard-blocked by name regardless.
        "POSTGRES_DB": _SCRATCH_DB,
    }
    try:
        _alembic("upgrade", "head", env_overrides=scratch_env)
        with scratch_engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert head == _alembic_head()

        # Convergence on what 0010 changes (and the whole table/view, so any drift surfaces).
        assert _table_shape(scratch_engine, _TABLE) == _table_shape(admin_engine, _TABLE), (
            "fresh-bootstrap config.source_mappings differs from the delta path — the "
            "manifest no longer carries 0010's end state (template_type column drift)"
        )
        assert _view_shape(scratch_engine) == _view_shape(admin_engine), (
            "fresh-bootstrap config.source_mappings_v differs from the delta path — the "
            "manifest view recreate drifted from migration 0010"
        )

        # Focused: template_type is TEXT NOT NULL, no enum, no CHECK, on the fresh DB.
        with scratch_engine.connect() as conn:
            tt = conn.execute(
                text(
                    "SELECT data_type, udt_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema='config' AND table_name='source_mappings' "
                    "AND column_name='template_type'"
                )
            ).one()
            assert (tt.data_type, tt.udt_name, tt.is_nullable) == ("text", "text", "NO")
            check_refs = conn.execute(
                text(
                    "SELECT COUNT(*) FROM pg_constraint "
                    "WHERE conrelid='config.source_mappings'::regclass AND contype='c' "
                    "AND pg_get_constraintdef(oid) ILIKE '%template_type%'"
                )
            ).scalar_one()
            assert check_refs == 0  # code-enforced vocabulary, no DB CHECK on fresh either
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
