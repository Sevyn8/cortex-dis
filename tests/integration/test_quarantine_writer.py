"""dis-quarantine against the live ithina_dis_db (Slice 11a).

WRITES to Postgres, so it runs only against ``ithina_dis_db`` on 5433 (never Customer
Master on 5432); the ``dis-rls`` target guard (inherited by the writer) refuses anything
else. Mirrors ``tests/integration/test_audit_writer.py``:

  * Load-bearing proofs must NOT silently skip when the stack is absent — a missing/
    unreachable stack is a loud ERROR (``StackRequiredError``), never ``pytest.skip``.
  * Read-backs use a RAW connection with a manual ``set_config`` — independent of the
    writer under test.
  * The FAIL-LOUD proof (the deliberate asymmetry with dis-audit's fire-and-forget)
    drives a REAL backend failure (FK violation) and asserts the raise + the rollback.
  * The RLS proof: a row held under tenant A is INVISIBLE under tenant B and visible
    under tenant A (FORCE RLS, the live tenant_isolation policy).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import dis_testing.fixtures as fx
from dis_core.errors import QuarantineWriteError
from dis_core.ids import new_uuid7
from dis_core.timestamps import now_utc
from dis_quarantine import (
    PostgresQuarantineWriter,
    QuarantinedChunk,
    QuarantinedRow,
    QuarantineFailureStage,
)

pytestmark = pytest.mark.integration

_TENANT_A = fx.TENANTS[0].uuid  # seeded FK targets in identity_mirror.tenants
_TENANT_B = fx.TENANTS[1].uuid


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the Slice 11a quarantine-writer tests (load-bearing "
            "RLS + fail-loud proofs) refuse to skip silently. Bring up the stack "
            "(make run-local) and export POSTGRES_URL (5433 / ithina_dis_db)."
        )
    from dis_rls import create_rls_engine
    from dis_testing.seed import seed_default_fixtures

    try:
        seed_default_fixtures(url=url)  # FK target tenants + the ACTIVE mapping; idempotent
    except Exception as exc:  # noqa: BLE001 — stack down → ERROR loudly, never skip
        raise StackRequiredError(
            f"DIS Postgres unreachable for the load-bearing quarantine-writer test ({exc!r}); "
            "refusing to skip. Bring up the stack (make run-local)."
        ) from exc

    eng = create_rls_engine(url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _read_chunks_as(engine: AsyncEngine, tenant_id: UUID, trace_id: UUID) -> list[dict[str, object]]:
    """Read held chunks via a RAW connection under ``tenant_id`` (NOT the writer)."""
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
            rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT tenant_id::text, status, failure_stage, failure_reason, "
                            "failure_context, mapping_version_id, resolved_at "
                            "FROM quarantine.quarantined_chunks WHERE trace_id = :tr"
                        ),
                        {"tr": str(trace_id)},
                    )
                )
                .mappings()
                .all()
            )
    return [dict(r) for r in rows]


def _chunk(tenant_id: UUID, trace_id: UUID) -> QuarantinedChunk:
    return QuarantinedChunk(
        tenant_id=tenant_id,
        store_id=None,
        data_ingress_event_id=new_uuid7(),
        trace_id=trace_id,
        source_id="sc_pos_v1",
        dis_channel="csv_upload",
        gcs_uri="gs://dis-bronze-local/tenant/x/object.csv",
        failure_stage=QuarantineFailureStage.MAPPING_LOOKUP,
        failure_reason="MAPPING_CONFIG_INVALID",
        failure_context={"failure_message": "no ACTIVE mapping (test)"},
        mapping_version_id=None,
        row_count_in_chunk=3,
        quarantined_at=now_utc(),
    )


async def _cleanup_trace(engine: AsyncEngine, tenant_id: UUID, trace_id: UUID) -> None:
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
            await conn.execute(
                text("DELETE FROM quarantine.quarantined_rows WHERE trace_id = :tr"),
                {"tr": str(trace_id)},
            )
            await conn.execute(
                text("DELETE FROM quarantine.quarantined_chunks WHERE trace_id = :tr"),
                {"tr": str(trace_id)},
            )


async def test_hold_chunk_lands_with_status_new_and_rls_scopes_it(engine: AsyncEngine) -> None:
    """The happy path + the tenant-isolation proof in one arc: held under A,
    status=NEW stamped by the DB default, INVISIBLE under B, visible under A."""
    trace_id = new_uuid7()
    try:
        await PostgresQuarantineWriter(engine).hold_chunk(_chunk(_TENANT_A, trace_id))

        # RLS: tenant B sees NOTHING (FORCE RLS + the tenant_isolation policy).
        assert await _read_chunks_as(engine, _TENANT_B, trace_id) == []

        # Tenant A sees the held chunk, exactly as written.
        rows = await _read_chunks_as(engine, _TENANT_A, trace_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["tenant_id"] == str(_TENANT_A)
        assert row["status"] == "NEW"  # the DB default — the writer never sends status
        assert row["resolved_at"] is None  # ck_qc_resolved_consistency holds for NEW
        assert row["failure_stage"] == "MAPPING_LOOKUP"
        assert row["failure_reason"] == "MAPPING_CONFIG_INVALID"
        # failure_context survives the JSON-string -> JSONB round-trip.
        assert row["failure_context"] == {"failure_message": "no ACTIVE mapping (test)"}
        assert row["mapping_version_id"] is None  # nullable on chunks (pre-lookup failure)
    finally:
        await _cleanup_trace(engine, _TENANT_A, trace_id)


async def test_hold_rows_lands_batch_in_one_transaction(engine: AsyncEngine) -> None:
    """The row form: one chunk's failing rows land together; mapping_version_id
    satisfies the live NOT NULL + FK to config.source_mappings."""
    trace_id = new_uuid7()
    ingress_id = new_uuid7()
    # The seeded ACTIVE mapping's id — the live FK target (read under tenant A).
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(_TENANT_A)})
            mapping_version_id = (
                await conn.execute(
                    text(
                        "SELECT mapping_version_id FROM config.source_mappings "
                        "WHERE tenant_id = :t AND status = 'ACTIVE' LIMIT 1"
                    ),
                    {"t": str(_TENANT_A)},
                )
            ).scalar_one()
    records = [
        QuarantinedRow(
            tenant_id=_TENANT_A,
            data_ingress_event_id=ingress_id,
            trace_id=trace_id,
            source_id="sc_pos_v1",
            dis_channel="csv_upload",
            gcs_uri="gs://dis-bronze-local/tenant/x/object.csv",
            row_offset=offset,
            failure_stage=QuarantineFailureStage.POST_MAPPING_VALIDATION,
            failure_reason="VALIDATION_ROW_FAILED",
            failure_context={"failures": [{"column": "quantity", "check": "ge(0)", "reason": "negative"}]},
            mapping_version_id=int(mapping_version_id),
            quarantined_at=now_utc(),
        )
        for offset in (2, 5)
    ]
    try:
        await PostgresQuarantineWriter(engine).hold_rows(records)
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(_TENANT_A)}
                )
                offsets = [
                    r[0]
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT row_offset FROM quarantine.quarantined_rows "
                                "WHERE trace_id = :tr ORDER BY row_offset"
                            ),
                            {"tr": str(trace_id)},
                        )
                    ).all()
                ]
        assert offsets == [2, 5]
    finally:
        await _cleanup_trace(engine, _TENANT_A, trace_id)


async def test_fail_loud_raises_on_real_backend_failure_and_rolls_back(engine: AsyncEngine) -> None:
    """The deliberate asymmetry with dis-audit: an UNSEEDED tenant passes the RLS
    WITH CHECK (tenant_id == app.tenant_id) but violates fk_qc_tenant at the
    backend — the writer RAISES QuarantineWriteError (the caller must nack) and
    the transaction rolled back (nothing held)."""
    orphan_tenant = new_uuid7()
    trace_id = new_uuid7()
    with pytest.raises(QuarantineWriteError) as excinfo:
        await PostgresQuarantineWriter(engine).hold_chunk(_chunk(orphan_tenant, trace_id))
    # Errors carry context (code-quality rule 5).
    assert excinfo.value.tenant_id == str(orphan_tenant)
    assert excinfo.value.trace_id == str(trace_id)
    assert excinfo.value.failure_code == "MAPPING_CONFIG_INVALID"
    # The row never landed — the failure was real and nothing partial survived.
    assert await _read_chunks_as(engine, orphan_tenant, trace_id) == []
