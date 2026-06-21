"""DB-pull integration: first-load, convergence, idempotence, and FK integrity.

Reads the in-cluster test Customer Master (``ithina_platform_db`` on 5433) and writes the DIS
database. Count/idempotence claims are checked by an **independent re-read of both sides** (raw
admin queries), never by the sync's own returned counts.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import Engine, Row, text
from sqlalchemy.exc import IntegrityError

import dis_testing.fixtures as fx
from dis_core.ids import new_uuid7
from dis_rls import create_rls_engine, rls_session
from dis_testing.customer_master_db import (
    CODELESS_EDGE_STORE_ID,
    delete_codeless_edge_store,
    insert_codeless_edge_store,
)
from mirror_sync_consumer.pull.runner import EXIT_OK, _run

pytestmark = pytest.mark.integration


def _by_id(engine: Engine, sql: str, key: str) -> Mapping[object, Row[Any]]:
    with engine.connect() as conn:
        return {getattr(r, key): r for r in conn.execute(text(sql)).all()}


_BRONZE_INSERT = text(
    """
    INSERT INTO bronze.data_ingress_events
        (id, tenant_id, store_id, source_id, dis_channel, trace_id, gcs_uri, received_at)
    VALUES
        (:id, :tid, :sid, 'manual_csv_upload', 'csv_upload', :trace, 'gs://test/x', now())
    """
)


async def test_first_load_mirrors_every_cm_record(run_env: None, cm_admin: Engine, dis_admin: Engine) -> None:
    # D55 faithful copy, NULL case: the baseline fixture set is all-coded (it
    # mirrors real Customer Master), so the nullable-store_code path is exercised
    # via a SCOPED edge inserted into the test-CM stand-in only — reverted in
    # teardown so the stand-in returns to its synced baseline (HARD REVERT RULE).
    insert_codeless_edge_store(cm_admin)
    try:
        assert await _run() == EXIT_OK

        cm_t = _by_id(cm_admin, "SELECT id, name, display_code, status FROM core.tenants", "id")
        mir_t = _by_id(
            dis_admin,
            "SELECT tenant_id, name, display_code, status FROM identity_mirror.tenants",
            "tenant_id",
        )
        assert set(cm_t) <= set(mir_t)
        for tid, row in cm_t.items():
            assert (mir_t[tid].name, mir_t[tid].display_code, mir_t[tid].status) == (
                row.name,
                row.display_code,
                row.status,
            )

        cm_s = _by_id(
            cm_admin,
            "SELECT id, name, store_code, status, country, timezone, currency, "
            "tax_treatment FROM core.stores",
            "id",
        )
        mir_s = _by_id(
            dis_admin,
            "SELECT store_id, name, store_code, status, country, timezone, currency, tax_treatment "
            "FROM identity_mirror.stores",
            "store_id",
        )
        assert set(cm_s) <= set(mir_s)
        for sid, row in cm_s.items():
            m = mir_s[sid]
            assert (m.name, m.store_code, m.status, m.country, m.timezone, m.currency, m.tax_treatment) == (
                row.name,
                row.store_code,
                row.status,
                row.country,
                row.timezone,
                row.currency,
                row.tax_treatment,
            )

        # D55 faithful copy, NULL case ASSERTED (never skipped): the scoped edge
        # store has store_code IS NULL in the stand-in; its mirror row must be NULL too.
        assert cm_s[CODELESS_EDGE_STORE_ID].store_code is None, "edge NULL store_code missing in test CM"
        assert mir_s[CODELESS_EDGE_STORE_ID].store_code is None

        # Count match on the CM-returned id set (independent re-read, not the sync's bookkeeping).
        assert sum(1 for t in mir_t if t in cm_t) == len(cm_t)
        assert sum(1 for s in mir_s if s in cm_s) == len(cm_s)
    finally:
        # HARD REVERT: drop the edge from BOTH the mirror and the stand-in baseline.
        with dis_admin.begin() as conn:
            conn.execute(
                text("DELETE FROM identity_mirror.stores WHERE store_id = :id"),
                {"id": str(CODELESS_EDGE_STORE_ID)},
            )
        delete_codeless_edge_store(cm_admin)


async def test_existing_rows_without_codes_are_backfilled(
    run_env: None, cm_admin: Engine, dis_admin: Engine
) -> None:
    """Backfill (Slice 9a): mirror rows that predate the code columns (NULL codes)
    gain display_code/store_code on the next normal sync run — no one-off step —
    and the run after that is a true no-op (idempotent by IS DISTINCT FROM)."""
    await _run()  # ensure rows exist

    tenant = fx.PRIMARY_TENANT
    coded_store = fx.PRIMARY_STORE
    # Simulate pre-9a rows: NULL the codes directly (independent admin write).
    # Failure-safe (hard revert rule): the NULL-ing + asserts run inside a try whose
    # finally ALWAYS restores the real codes, so a mid-test raise (or a failing _run)
    # cannot leave the SHARED primary identity (buc-ees / TX-101) dirty for the rest
    # of the session. Matches the edge-fixture try/finally pattern. The body's re-sync
    # also restores; the finally guarantees it even off the happy path.
    try:
        with dis_admin.begin() as conn:
            conn.execute(
                text("UPDATE identity_mirror.tenants SET display_code = NULL WHERE tenant_id = :id"),
                {"id": str(tenant.uuid)},
            )
            conn.execute(
                text("UPDATE identity_mirror.stores SET store_code = NULL WHERE store_id = :id"),
                {"id": str(coded_store.uuid)},
            )

        assert await _run() == EXIT_OK

        with dis_admin.connect() as conn:
            backfilled_t = conn.execute(
                text("SELECT display_code FROM identity_mirror.tenants WHERE tenant_id = :id"),
                {"id": str(tenant.uuid)},
            ).scalar_one()
            backfilled_s = conn.execute(
                text("SELECT store_code FROM identity_mirror.stores WHERE store_id = :id"),
                {"id": str(coded_store.uuid)},
            ).scalar_one()
        assert backfilled_t == tenant.display_code
        assert backfilled_s == coded_store.store_code

        # Idempotence: the next run rewrites nothing (mirror_synced_at untouched).
        def synced() -> tuple[object, object]:
            with dis_admin.connect() as conn:
                t = conn.execute(
                    text("SELECT mirror_synced_at FROM identity_mirror.tenants WHERE tenant_id = :id"),
                    {"id": str(tenant.uuid)},
                ).scalar_one()
                s = conn.execute(
                    text("SELECT mirror_synced_at FROM identity_mirror.stores WHERE store_id = :id"),
                    {"id": str(coded_store.uuid)},
                ).scalar_one()
            return t, s

        before = synced()
        assert await _run() == EXIT_OK
        assert synced() == before
    finally:
        # Restore the shared primary's real codes unconditionally (idempotent direct
        # write); a failure above must not poison buc-ees / TX-101 for later tests.
        with dis_admin.begin() as conn:
            conn.execute(
                text("UPDATE identity_mirror.tenants SET display_code = :c WHERE tenant_id = :id"),
                {"c": tenant.display_code, "id": str(tenant.uuid)},
            )
            conn.execute(
                text("UPDATE identity_mirror.stores SET store_code = :c WHERE store_id = :id"),
                {"c": coded_store.store_code, "id": str(coded_store.uuid)},
            )


async def test_rerun_without_change_is_a_noop(run_env: None, cm_admin: Engine, dis_admin: Engine) -> None:
    await _run()

    def synced_at() -> tuple[dict[object, object], dict[object, object]]:
        t = _by_id(dis_admin, "SELECT tenant_id, mirror_synced_at FROM identity_mirror.tenants", "tenant_id")
        s = _by_id(dis_admin, "SELECT store_id, mirror_synced_at FROM identity_mirror.stores", "store_id")
        return (
            {k: v.mirror_synced_at for k, v in t.items()},
            {k: v.mirror_synced_at for k, v in s.items()},
        )

    before_t, before_s = synced_at()
    assert await _run() == EXIT_OK
    after_t, after_s = synced_at()

    cm_t = set(_by_id(cm_admin, "SELECT id FROM core.tenants", "id"))
    cm_s = set(_by_id(cm_admin, "SELECT id FROM core.stores", "id"))
    for tid in cm_t:
        assert before_t[tid] == after_t[tid]  # unchanged row not rewritten
    for sid in cm_s:
        assert before_s[sid] == after_s[sid]


async def test_rerun_after_cm_change_converges(run_env: None, cm_admin: Engine, dis_admin: Engine) -> None:
    await _run()  # baseline
    tenant = fx.TENANTS[0]
    tid = str(tenant.uuid)
    new_store = str(new_uuid7())

    # CM-side change: add a store, change a tenant name. NO tenant reassignment (directive 3 —
    # a store never moves tenants).
    with cm_admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO core.stores (id, tenant_id, name, status, country, timezone, "
                "currency, tax_treatment, created_at, updated_at) VALUES "
                "(:id, :tid, 'New Store', 'ACTIVE', 'US', 'America/New_York', 'USD', "
                "'EXCLUSIVE', now(), now())"
            ),
            {"id": new_store, "tid": tid},
        )
        conn.execute(
            text("UPDATE core.tenants SET name = 'Renamed Co', updated_at = now() WHERE id = :id"),
            {"id": tid},
        )
    try:
        assert await _run() == EXIT_OK
        with dis_admin.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT name FROM identity_mirror.tenants WHERE tenant_id = :id"),
                    {"id": tid},
                ).scalar_one()
                == "Renamed Co"
            )
            assert (
                conn.execute(
                    text("SELECT count(*) FROM identity_mirror.stores WHERE store_id = :id"),
                    {"id": new_store},
                ).scalar_one()
                == 1
            )
        # No duplicates / no deletions: every CM store id is present exactly once in the mirror.
        cm_store_ids = set(_by_id(cm_admin, "SELECT id FROM core.stores", "id"))
        mir_store_ids = set(_by_id(dis_admin, "SELECT store_id FROM identity_mirror.stores", "store_id"))
        assert cm_store_ids <= mir_store_ids
    finally:
        with cm_admin.begin() as conn:
            conn.execute(text("DELETE FROM core.stores WHERE id = :id"), {"id": new_store})
            conn.execute(
                text("UPDATE core.tenants SET name = :n, updated_at = now() WHERE id = :id"),
                {"n": tenant.name, "id": tid},
            )
        with dis_admin.begin() as conn:
            conn.execute(text("DELETE FROM identity_mirror.stores WHERE store_id = :id"), {"id": new_store})
            conn.execute(
                text("UPDATE identity_mirror.tenants SET name = :n WHERE tenant_id = :id"),
                {"n": tenant.name, "id": tid},
            )


async def test_mirrored_rows_satisfy_composite_store_fk(run_env: None, dis_admin: Engine) -> None:
    # Criterion 7: a write referencing a SYNCED (tenant, store) succeeds; an absent identity
    # fails the composite FK. The referenced identity comes from the sync run, not the seeder.
    assert await _run() == EXIT_OK
    store = fx.STORES[0]
    tid = str(fx.tenant_uuid_for(store.tenant_display_code))
    sid = str(store.uuid)
    row_id = str(new_uuid7())
    engine = create_rls_engine(os.environ["POSTGRES_URL"])
    try:
        async with rls_session(engine, tid) as conn:
            await conn.execute(
                _BRONZE_INSERT, {"id": row_id, "tid": tid, "sid": sid, "trace": str(new_uuid7())}
            )
        with pytest.raises(IntegrityError):
            async with rls_session(engine, tid) as conn:
                await conn.execute(
                    _BRONZE_INSERT,
                    {"id": str(new_uuid7()), "tid": tid, "sid": str(new_uuid7()), "trace": str(new_uuid7())},
                )
    finally:
        with dis_admin.begin() as conn:
            conn.execute(text("DELETE FROM bronze.data_ingress_events WHERE id = :id"), {"id": row_id})
        await engine.dispose()
