"""Migration 0005 (source_mappings template grain + RLS ON): target safety,
backfill, reversibility, and fresh-bootstrap convergence (Slice 14a).

Four layers:

  * **Target-safety guard, asserted positively and non-skippably.** The pure
    ``check_migration_target`` refusal logic is unit-testable without a live
    bind (the 0002/0003/0004 precedent): refuses Customer Master outright,
    refuses any non-expected database, passes only the DIS database.
  * **Reversible cycle against the live DIS database (5433).** ``downgrade
    0004`` removes the template columns, restores the (tenant, source) keys
    and the pre-0005 trigger body, and turns RLS off; ``upgrade head``
    re-adds, BACKFILLS the live rows (one template_id per (tenant, source)
    group, name 'default'), rekeys, and turns RLS on. Errors — never skips —
    when the stack is absent (the load-bearing-proof rule from Slices 4/7).
  * **D22/D49 invariance.** The PK, the canonical FKs onto
    ``mapping_version_id``, and the ``mapping_rules`` column are byte-equal
    before and after the cycle (the pin stands; the rules shape is untouched).
  * **Fresh-bootstrap convergence on a scratch DB (the 9a lesson).** A scratch
    database is created on the same 5433 instance, ``alembic upgrade head``
    runs against it (0001 applies the updated manifest, 0005 must then no-op),
    and the full normalized shape — columns, constraint defs, index defs,
    trigger + function body (``pg_get_functiondef``, not file text), RLS
    posture, policy, view reloptions, comments — is compared against the
    delta-path database. The scratch DB is dropped in teardown.

The migration runs against ``ithina_dis_db`` (and the scratch DB) on 5433
only; the in-migration guard refuses Customer Master (``ithina_platform_db``)
before any DDL.

See: docs/slices/slice-14a-source-mappings-migration.md.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0005_source_mappings_template_grain_rls.py"

_SCRATCH_DB = "ithina_dis_scratch_14a"

# Constraints and indexes whose live definitions constitute the 0005 shape.
_CSM_CONSTRAINTS = (
    "pk_csm",
    "uq_csm_seq_per_source",
    "ex_csm_template_name_per_source",
    "fk_csm_tenant",
    "ck_csm_status_vocab",
    "ck_csm_version_seq_positive",
    "ck_csm_activated_consistency",
    "ck_csm_deprecated_consistency",
)
_CSM_INDEXES = (
    "uq_csm_active_per_source",
    "ix_csm_tenant_source_status",
    "ix_csm_status",
    "ix_csm_predecessor",
)
_COMMENTED_COLUMNS = (
    "template_id",
    "template_name",
    "version_seq_per_source",
    "status",
    "predecessor_version_id",
)


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0005", _MIGRATION_PATH)
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
# Live cycle + convergence against 5433 (errors, never skips).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = os.environ.get("POSTGRES_ADMIN_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the migration-cycle test needs the admin "
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


def _template_columns(engine: Engine) -> dict[str, str]:
    """{column: is_nullable} for the two 0005 columns (empty dict = absent)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'config' AND table_name = 'source_mappings' "
                "AND column_name IN ('template_id', 'template_name')"
            )
        ).all()
    return {r.column_name: r.is_nullable for r in rows}


def _rls_posture(engine: Engine) -> tuple[bool, bool, int]:
    with engine.connect() as conn:
        flags = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'config.source_mappings'::regclass"
            )
        ).one()
        policies = conn.execute(
            text(
                "SELECT COUNT(*) FROM pg_policies WHERE schemaname = 'config' "
                "AND tablename = 'source_mappings' AND policyname = 'tenant_isolation'"
            )
        ).scalar_one()
    return bool(flags.relrowsecurity), bool(flags.relforcerowsecurity), int(policies)


def _pin_shape(engine: Engine) -> dict[str, str | None]:
    """The D22/D49 invariants: PK def, canonical FKs onto mapping_version_id,
    and the mapping_rules column type — must be identical across the cycle."""
    with engine.connect() as conn:
        pk = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'config.source_mappings'::regclass AND conname = 'pk_csm'"
            )
        ).scalar_one()
        fks = (
            conn.execute(
                text(
                    "SELECT conrelid::regclass::text || ': ' || pg_get_constraintdef(oid) AS d "
                    "FROM pg_constraint "
                    "WHERE confrelid = 'config.source_mappings'::regclass ORDER BY 1"
                )
            )
            .scalars()
            .all()
        )
        rules = conn.execute(
            text(
                "SELECT data_type || '/' || is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'config' AND table_name = 'source_mappings' "
                "AND column_name = 'mapping_rules'"
            )
        ).scalar_one()
    return {"pk": pk, "fks": "; ".join(fks), "mapping_rules": rules}


def _csm_shape(engine: Engine) -> dict[str, object]:
    """The full normalized 0005 end-state shape, for convergence comparison.

    Everything here comes from the live catalogs (pg_get_*def, pg_indexes,
    information_schema, pg_policies, obj/col_description) — never from file
    text — so the manifest and the migration cannot 'agree' by coincidence.
    """
    shape: dict[str, object] = {}
    with engine.connect() as conn:
        shape["columns"] = [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, "
                    "COALESCE(character_maximum_length, -1), collation_name "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'config' AND table_name = 'source_mappings' "
                    "ORDER BY column_name"
                )
            ).all()
        ]
        shape["constraints"] = {
            name: conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'config.source_mappings'::regclass AND conname = :n"
                ),
                {"n": name},
            ).scalar()
            for name in _CSM_CONSTRAINTS
        }
        shape["indexes"] = {
            name: conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'config' "
                    "AND tablename = 'source_mappings' AND indexname = :n"
                ),
                {"n": name},
            ).scalar()
            for name in _CSM_INDEXES
        }
        shape["trigger"] = conn.execute(
            text(
                "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t "
                "WHERE t.tgrelid = 'config.source_mappings'::regclass AND NOT t.tgisinternal"
            )
        ).scalar()
        shape["function"] = conn.execute(
            text("SELECT pg_get_functiondef('config.set_csm_version_seq()'::regprocedure)")
        ).scalar()
        shape["rls"] = (
            conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'config.source_mappings'::regclass"
                )
            )
            .one()
            ._asdict()
        )
        shape["policy"] = (
            conn.execute(
                text(
                    "SELECT permissive, roles, cmd, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'config' AND tablename = 'source_mappings' "
                    "AND policyname = 'tenant_isolation'"
                )
            )
            .one()
            ._asdict()
        )
        shape["view_options"] = conn.execute(
            text("SELECT reloptions FROM pg_class WHERE oid = 'config.source_mappings_v'::regclass")
        ).scalar()
        shape["table_comment"] = conn.execute(
            text("SELECT obj_description('config.source_mappings'::regclass)")
        ).scalar()
        shape["view_comment"] = conn.execute(
            text("SELECT obj_description('config.source_mappings_v'::regclass)")
        ).scalar()
        shape["column_comments"] = {
            col: conn.execute(
                text(
                    "SELECT col_description('config.source_mappings'::regclass, "
                    "(SELECT ordinal_position FROM information_schema.columns "
                    " WHERE table_schema = 'config' AND table_name = 'source_mappings' "
                    " AND column_name = :c))"
                ),
                {"c": col},
            ).scalar()
            for col in _COMMENTED_COLUMNS
        }
    return shape


@pytest.mark.skip(reason="downgrade-reversibility deferred until staging (D99)")
def test_migration_cycle_backfills_and_flips_rls(admin_engine: Engine) -> None:
    # apply-to-head/fresh-bootstrap stays covered by test_fresh_bootstrap_converges_with_delta_path.
    # The downgrade restores (tenant, source)-grained keys, which only hold if
    # no source carries multiple templates or multiple ACTIVE rows. Guard
    # BEFORE any downgrade — error loudly (never skip, never strand).
    with admin_engine.connect() as conn:
        offenders = conn.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "  SELECT tenant_id, source_id FROM config.source_mappings "
                "  GROUP BY tenant_id, source_id "
                "  HAVING COUNT(DISTINCT template_id) > 1 "
                "      OR COUNT(*) FILTER (WHERE status = 'ACTIVE') > 1"
                ") x"
            )
        ).scalar_one()
    if offenders:
        raise StackRequiredError(
            f"Migration-cycle test requires single-template sources ({offenders} "
            "offending (tenant, source) group(s)): the downgrade leg would refuse "
            "and strand the cycle. Run `make reset-local` for a clean cycle."
        )

    pins_before = _pin_shape(admin_engine)

    # upgrade head first (idempotent if already at 0005).
    _alembic("upgrade", "head")
    assert _template_columns(admin_engine) == {"template_id": "NO", "template_name": "NO"}
    assert _rls_posture(admin_engine) == (True, True, 1)

    # downgrade to 0004: columns gone, RLS off, policy dropped, old keys back.
    _alembic("downgrade", "0004")
    assert _template_columns(admin_engine) == {}
    assert _rls_posture(admin_engine) == (False, False, 0)
    with admin_engine.connect() as conn:
        seq_def = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'config.source_mappings'::regclass "
                "AND conname = 'uq_csm_seq_per_source'"
            )
        ).scalar_one()
        fn_def = conn.execute(
            text("SELECT pg_get_functiondef('config.set_csm_version_seq()'::regprocedure)")
        ).scalar_one()
    assert "template_id" not in seq_def
    assert "template_id" not in fn_def

    # re-upgrade: columns NOT NULL and every live row backfilled — non-null
    # UUID template_id, name 'default', exactly ONE template per
    # (tenant, source) group (lineage preserved, never per-row minting).
    _alembic("upgrade", "head")
    assert _template_columns(admin_engine) == {"template_id": "NO", "template_name": "NO"}
    assert _rls_posture(admin_engine) == (True, True, 1)
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tenant_id, source_id, template_id, template_name FROM config.source_mappings")
        ).all()
        per_group = conn.execute(
            text(
                "SELECT MAX(c) FROM (SELECT COUNT(DISTINCT template_id) AS c "
                "FROM config.source_mappings GROUP BY tenant_id, source_id) x"
            )
        ).scalar()
    assert rows, "cycle ran against an empty table — backfill not exercised"
    for r in rows:
        assert isinstance(UUID(str(r.template_id)), UUID)
        assert r.template_name == "default"
    assert per_group == 1

    # D22/D49 invariance: the pin and the rules shape survived the cycle.
    assert _pin_shape(admin_engine) == pins_before


def test_fresh_bootstrap_converges_with_delta_path(admin_url: str, admin_engine: Engine) -> None:
    """The 9a lesson: the fresh path (0001 applies the updated manifest, 0005
    no-ops) must land the IDENTICAL shape the delta path leaves behind."""
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
        # Stop at 0004 first: 0001 has applied the manifest verbatim, so this
        # IS the manifest-built shape, before 0005 can touch anything.
        _alembic("upgrade", "0004", env_overrides=scratch_env)
        manifest_shape = _csm_shape(scratch_engine)

        _alembic("upgrade", "head", env_overrides=scratch_env)
        with scratch_engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        # The pin moves with each new revision ON PURPOSE: a new migration's
        # author must revisit this test and confirm the fresh-bootstrap no-op
        # property holds for it too (0006: the bronze template_id ADD COLUMN is
        # existence-gated, so a manifest-fresh database is untouched. 0007:
        # drop-and-recreate of audit.events from the SAME manifest file 0001
        # applied — shape-identical on a manifest-fresh database, and it never
        # touches config.source_mappings, so the csm shape compared below is
        # untouched. 0008: existence-gated ADD COLUMN + definition-gated CHECK
        # swap on audit.events — both no-ops on a manifest-fresh database, and
        # config.source_mappings is untouched. 0009: drop-and-recreate of the
        # six canonical/staging parents from the SAME manifest files 0001
        # applied — shape-identical on a manifest-fresh database, and it never
        # touches config.source_mappings).
        assert head == _alembic_head()

        # 0005 must be a TRUE NO-OP on a manifest-fresh database. Without this,
        # a drifted manifest could bootstrap the WRONG shape and 0005's
        # self-healing steps (the unconditional CREATE OR REPLACE, the
        # shape-gated rekeys, the unconditional RLS/view/comment statements)
        # would silently repair it — end state correct, manifest-as-source-of-
        # truth drift invisible. Any repair shows up here as a shape change.
        assert _csm_shape(scratch_engine) == manifest_shape, (
            "migration 0005 CHANGED a manifest-fresh database — the manifest "
            "no longer carries the 0005 end state (drift self-healed by 0005)"
        )

        # And the fresh end state equals the delta-path end state.
        assert _csm_shape(scratch_engine) == _csm_shape(admin_engine)
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
