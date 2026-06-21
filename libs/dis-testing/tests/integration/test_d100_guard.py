"""Bite proof for the D100 post-suite clean-state guard.

DB-backed and stack-gated (skips when ``POSTGRES_ADMIN_URL`` is unset), like the other
integration tests in this dir. Each test injects ONE residue row, asserts the guard FAILS
and NAMES that specific leaker, reverts the row, then asserts the guard no longer names it.

Assertions key on the test's OWN marker (its trace_id / source_id) rather than on the guard
being globally clean — mid-suite the shared DB legitimately carries the session-scoped consumer
mappings, so a global clean-state precondition would make these tests skip. ``identity_mirror``
is intentionally not exercised — it is excluded from the guard (resident real-CM baseline).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from uuid_utils import uuid7

from dis_testing import fixtures as fx
from dis_testing.plugin import SuiteResidueError, _assert_clean_shared_db_after_suite

_RULES = '{"version": 1, "rename": {}, "normalize": {}, "cast": {}, "derive": {}}'


@pytest.fixture
def admin_url() -> str:
    url = os.environ.get("POSTGRES_ADMIN_URL")
    if not url:
        pytest.skip("POSTGRES_ADMIN_URL not set — the D100 guard bite proof needs the admin stack")
    return url


@pytest.fixture
def admin_engine(admin_url: str) -> Iterator[Engine]:
    engine = create_engine(admin_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _guard_report(admin_url: str) -> str:
    """Run the guard; return its failure report text, or '' if it passed clean."""
    try:
        _assert_clean_shared_db_after_suite(admin_url)
    except SuiteResidueError as exc:
        return str(exc)
    return ""


def test_guard_detects_empty_table_residue(admin_url: str, admin_engine: Engine) -> None:
    trace = uuid7()
    insert = text(
        """
        INSERT INTO bronze.data_ingress_events
            (id, tenant_id, source_id, dis_channel, trace_id, gcs_uri, received_at)
        VALUES (:id, :tid, 'manual_csv_upload', 'csv_upload', :trace, :gcs, now())
        """
    )
    with admin_engine.begin() as conn:
        conn.execute(
            insert,
            {
                "id": str(uuid7()),
                "tid": str(fx.PRIMARY_TENANT.uuid),
                "trace": str(trace),
                "gcs": f"gs://d100-bite/{trace}.csv",
            },
        )
    try:
        report = _guard_report(admin_url)
        assert "bronze.data_ingress_events" in report
        assert str(trace) in report
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM bronze.data_ingress_events WHERE trace_id = :t"),
                {"t": str(trace)},
            )
    assert str(trace) not in _guard_report(admin_url)


def test_guard_detects_staging_residue(admin_url: str, admin_engine: Engine) -> None:
    """Probe-1 fix: a row in a staging.* mirror table is now caught (was uncovered)."""
    trace = uuid7()
    insert = text(
        """
        INSERT INTO staging.store_sku_signal_history
            (as_of_date, tenant_id, store_id, sku_id, trace_id)
        VALUES (CURRENT_DATE, :tid, :sid, 'D100-BITE-SKU', :trace)
        """
    )
    with admin_engine.begin() as conn:
        conn.execute(
            insert,
            {
                "tid": str(fx.PRIMARY_TENANT.uuid),
                "sid": str(fx.PRIMARY_STORE.uuid),
                "trace": str(trace),
            },
        )
    try:
        report = _guard_report(admin_url)
        assert "staging.store_sku_signal_history" in report
        assert str(trace) in report
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM staging.store_sku_signal_history WHERE trace_id = :t"),
                {"t": str(trace)},
            )
    assert str(trace) not in _guard_report(admin_url)


def test_guard_detects_non_baseline_source_mapping(admin_url: str, admin_engine: Engine) -> None:
    source_id = f"d100_bite_{uuid7().hex[:12]}"
    insert = text(
        """
        INSERT INTO config.source_mappings
            (tenant_id, source_id, template_id, template_name, template_type, status,
             mapping_rules, activated_at)
        VALUES (:t, :s, :tpl, 'default', 'sales', 'ACTIVE', CAST(:rules AS JSONB), NOW())
        """
    )
    with admin_engine.begin() as conn:
        conn.execute(
            insert,
            {
                "t": str(fx.PRIMARY_TENANT.uuid),
                "s": source_id,
                "tpl": str(fx.DEFAULT_TEMPLATE_ID),
                "rules": _RULES,
            },
        )
    try:
        report = _guard_report(admin_url)
        assert "config.source_mappings" in report
        assert source_id in report
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM config.source_mappings WHERE source_id = :s"),
                {"s": source_id},
            )
    assert source_id not in _guard_report(admin_url)


def test_guard_detects_duplicate_baseline_triple_mapping(admin_url: str, admin_engine: Engine) -> None:
    """Probe-2 fix: a SECOND row sharing the baseline (tenant, source, template) — a DEPRECATED
    prior version — is now caught. The old triple-membership predicate let this escape; the
    completeness check counts rows per baseline triple and flags the duplicate."""
    # A DEPRECATED v2 under the baseline triple: shares (tenant, source, template) but avoids
    # uq_csm_active_per_source (ACTIVE-only) and ex_csm_template_name_per_source (same template_id,
    # DEPRECATED excluded). The version_seq trigger assigns the next seq.
    insert = text(
        """
        INSERT INTO config.source_mappings
            (tenant_id, source_id, template_id, template_name, template_type, status,
             mapping_rules, activated_at, deprecated_at)
        VALUES (:t, :s, :tpl, 'default', 'sales', 'DEPRECATED', CAST(:rules AS JSONB), NOW(), NOW())
        """
    )
    with admin_engine.begin() as conn:
        conn.execute(
            insert,
            {
                "t": str(fx.PRIMARY_TENANT.uuid),
                "s": fx.DEFAULT_SOURCE_ID,
                "tpl": str(fx.DEFAULT_TEMPLATE_ID),
                "rules": _RULES,
            },
        )
    try:
        report = _guard_report(admin_url)
        assert "config.source_mappings" in report
        assert "share one baseline triple" in report
        assert fx.DEFAULT_SOURCE_ID in report
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM config.source_mappings WHERE source_id = :s AND status = 'DEPRECATED'"),
                {"s": fx.DEFAULT_SOURCE_ID},
            )
    # The seeded ACTIVE baseline (count 1 under its triple) must NOT be flagged (case iii).
    assert "share one baseline triple" not in _guard_report(admin_url)
