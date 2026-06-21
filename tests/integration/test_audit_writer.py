"""dis-audit against the live ithina_dis_db (Slice 6 AC2/AC3/AC4/AC6).

WRITES to Postgres, so it runs only against ``ithina_dis_db`` on 5433 (never Customer
Master on 5432); the ``dis-rls`` target guard (inherited by the writer) refuses anything
else. Mirrors ``tests/integration/test_rls_isolation.py``:

  * Load-bearing proofs must NOT silently skip when the stack is absent — a missing/
    unreachable stack is a loud ERROR (``StackRequiredError``), never ``pytest.skip``.
  * The drift guards derive truth from live introspection (information_schema /
    pg_constraint), not from the DDL file or any snapshot.
  * Read-backs use a RAW connection with a manual ``set_config`` — independent of the
    writer under test, so the happy-path test does not merely agree with itself.
  * The fire-and-forget proof drives a REAL backend failure (FK violation) so the swallow
    is exercised, not bypassed; it errors if the failing backend cannot be established.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import dis_testing.fixtures as fx
from dis_audit import (
    EXPECTED_COLUMNS,
    AuditEvent,
    EventScope,
    Outcome,
    PostgresAuditWriter,
    Stage,
    diff_schema,
)
from dis_core.ids import new_uuid7
from dis_core.timestamps import now_utc
from dis_rls import create_rls_engine

pytestmark = pytest.mark.integration

_TENANT = str(fx.TENANTS[0].uuid)  # seeded FK target in identity_mirror.tenants


class StackRequiredError(RuntimeError):
    """The DIS stack is required for this load-bearing test but is absent."""


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the Slice 6 audit-writer tests (load-bearing AC3/AC4) "
            "refuse to skip silently. Bring up the stack (make run-local) and export "
            "POSTGRES_URL (5433 / ithina_dis_db)."
        )
    from dis_testing.seed import seed_default_fixtures

    try:
        seed_default_fixtures(url=url)  # FK target tenant; idempotent
    except Exception as exc:  # noqa: BLE001 — stack down → ERROR loudly, never skip
        raise StackRequiredError(
            f"DIS Postgres unreachable for the load-bearing audit-writer test ({exc!r}); "
            "refusing to skip. Bring up the stack (make run-local)."
        ) from exc

    eng = create_rls_engine(url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _read_row_by_trace(engine: AsyncEngine, tenant_id: str, trace_id: str) -> dict[str, object] | None:
    """Read one audit row via a RAW connection (NOT the writer) — independent check."""
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT tenant_id::text, trace_id::text, stage, event_scope, outcome, "
                            "event_date::text, row_count, event_data FROM audit.events "
                            "WHERE trace_id = :tr"
                        ),
                        {"tr": trace_id},
                    )
                )
                .mappings()
                .first()
            )
    return dict(row) if row is not None else None


# ---- AC2 (HARDENED, Slice 30c): the live schema matches the frozen contract at FULL
# shape grain — names both directions PLUS type, nullability, and character length.
# Pre-30c this guard was a column-NAME-set match only, so a type narrowing or a
# nullability flip passed the guard and died silently at INSERT under fire-and-forget
# (the D45 silent-loss class). The pure diff_schema is narrowing-proven in the lib's
# unit tests; here it runs against the REAL information_schema.
async def test_live_schema_matches_contract_full_shape(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        live_rows = [
            (r[0], r[1], r[2], r[3])
            for r in (
                await conn.execute(
                    text(
                        "SELECT column_name, data_type, is_nullable, character_maximum_length "
                        "FROM information_schema.columns "
                        "WHERE table_schema='audit' AND table_name='events'"
                    )
                )
            ).all()
        ]
    diffs = diff_schema(live_rows, EXPECTED_COLUMNS)
    assert not diffs, "audit.events drifted from the dis-audit schema contract:\n" + "\n".join(diffs)
    # And the model agrees with the contract's column-name set (model <-> contract
    # <-> live tie transitively; the model carries no type info to compare).
    assert AuditEvent.db_column_names() == set(EXPECTED_COLUMNS)


# ---- AC6: Outcome / EventScope membership equals the live CHECK vocab --------------------
async def _check_vocab(engine: AsyncEngine, conname: str) -> set[str]:
    async with engine.connect() as conn:
        # Scope to the parent table: partitions inherit a same-named CHECK, so an
        # unscoped query returns one row per partition.
        definition = (
            await conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = :n AND conrelid = 'audit.events'::regclass"
                ),
                {"n": conname},
            )
        ).scalar_one()
    return set(re.findall(r"'([A-Z_]+)'::", definition))


async def test_outcome_vocab_matches_live_check(engine: AsyncEngine) -> None:
    live = await _check_vocab(engine, "ck_audit_events_outcome_vocab")
    assert {o.value for o in Outcome} == live
    # FLIPPED by Slice 30c (the D42 revision): the D33 duplicate outcomes are now
    # first-class in the live CHECK — promoted from event_data for console
    # queryability, superseding the Slice-10 JSONB resolution.
    assert "DUPLICATE_NOOP" in live and "DUPLICATE_OVERWRITTEN" in live


async def test_event_scope_vocab_matches_live_check(engine: AsyncEngine) -> None:
    live = await _check_vocab(engine, "ck_audit_events_event_scope_vocab")
    assert {s.value for s in EventScope} == live


async def _cleanup_audit_trace(engine: AsyncEngine, tenant_id: str, trace_id: str) -> None:
    """Revert the audit.events rows a test inserted (the test_quarantine_writer idiom).
    audit.events is FORCE RLS; the tenant GUC scopes the DELETE. A test that mutates the
    shared live DB restores it (D100)."""
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            await conn.execute(text("DELETE FROM audit.events WHERE trace_id = :tr"), {"tr": trace_id})


# ---- AC3: the writer lands a row; verified by independent read-back ----------------------
async def test_writer_lands_audit_event(engine: AsyncEngine) -> None:
    trace_id = new_uuid7()
    try:
        event = AuditEvent(
            event_timestamp=now_utc(),
            trace_id=trace_id,
            tenant_id=fx.TENANTS[0].uuid,
            service_name="streaming-consumer",
            stage=Stage.CANONICAL_WRITTEN,
            event_scope=EventScope.INGRESS_EVENT,
            outcome=Outcome.SUCCESS,
            row_count=5,
            event_data={"written_to_table": "store_sku_sale_events"},
        )
        assert await PostgresAuditWriter(engine).write(event) is True

        row = await _read_row_by_trace(engine, _TENANT, str(trace_id))
        assert row is not None, "audit row was not landed"
        assert row["tenant_id"] == _TENANT
        assert row["stage"] == "CANONICAL_WRITTEN"
        assert row["event_scope"] == "INGRESS_EVENT"
        assert row["outcome"] == "SUCCESS"
        assert row["row_count"] == 5
        assert row["event_date"] == event.event_date.isoformat()  # type: ignore[union-attr]
        # event_data survives the JSON-string -> JSONB -> dict round-trip through the real DB.
        assert row["event_data"] == {"written_to_table": "store_sku_sale_events"}
    finally:
        await _cleanup_audit_trace(engine, _TENANT, str(trace_id))


# ---- AC3 (boundary): event_date derived across the UTC date line passes the live CHECK ----
async def test_writer_lands_event_crossing_utc_date_boundary(engine: AsyncEngine) -> None:
    # 2026-06-04 01:30 +05:30 == 2026-06-03 20:00 UTC: local date and UTC date differ. The live
    # ck_audit_events_event_date_matches CHECK (event_date = (ts AT TIME ZONE 'UTC')::date) must
    # accept the model's UTC-derived date on a REAL insert (plain table since Slice 30a; any date lands).
    ist = timezone(timedelta(hours=5, minutes=30))
    trace_id = new_uuid7()
    try:
        event = AuditEvent(
            event_timestamp=datetime(2026, 6, 4, 1, 30, tzinfo=ist),
            trace_id=trace_id,
            tenant_id=fx.TENANTS[0].uuid,
            service_name="streaming-consumer",
            stage=Stage.MAPPING_EXECUTED,
            event_scope=EventScope.INGRESS_EVENT,
            outcome=Outcome.SUCCESS,
        )
        assert event.event_date is not None and event.event_date.isoformat() == "2026-06-03"
        assert await PostgresAuditWriter(engine).write(event) is True, "live CHECK rejected the UTC date"
        row = await _read_row_by_trace(engine, _TENANT, str(trace_id))
        assert row is not None and row["event_date"] == "2026-06-03"
    finally:
        await _cleanup_audit_trace(engine, _TENANT, str(trace_id))


# ---- AC4: fire-and-forget proven against a REAL failing backend (non-vacuous) -----------
async def test_fire_and_forget_swallows_real_backend_failure(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    # An unseeded tenant: RLS WITH CHECK passes (tenant_id == app.tenant_id), but
    # fk_audit_events_tenant -> identity_mirror.tenants raises IntegrityError at the backend.
    # This reaches Postgres and fails there, so the swallow is exercised, not bypassed.
    orphan_tenant = new_uuid7()
    trace_id = new_uuid7()
    event = AuditEvent(
        event_timestamp=now_utc(),
        trace_id=trace_id,
        tenant_id=orphan_tenant,
        service_name="streaming-consumer",
        stage=Stage.CANONICAL_WRITTEN,
        event_scope=EventScope.INGRESS_EVENT,
        outcome=Outcome.FAILURE,
        failure_code="FK_TEST",
    )

    with caplog.at_level(logging.ERROR, logger="dis-audit"):
        result = await PostgresAuditWriter(engine).write(event)  # must NOT raise

    assert result is False, "fire-and-forget writer must report failure, not success"

    errors = [r for r in caplog.records if r.levelno == logging.ERROR and r.name == "dis-audit"]
    assert errors, "the swallowed failure must be logged as an error (worth alerting)"
    rec = errors[-1]
    assert getattr(rec, "trace_id", None) == str(trace_id)
    assert getattr(rec, "tenant_id", None) == str(orphan_tenant)
    assert getattr(rec, "stage", None) == "CANONICAL_WRITTEN"

    # The row never landed (the FK-violating transaction rolled back) — proves the failure
    # was real and the swallow did not mask a silent partial write.
    assert await _read_row_by_trace(engine, str(orphan_tenant), str(trace_id)) is None


async def test_fire_and_forget_refuses_tenantless_event(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    # Product rule D43: no tenant-less audit path. A None tenant is refused loudly (logged),
    # never a silent drop; the writer still does not raise to the caller.
    event = AuditEvent(
        event_timestamp=now_utc(),
        trace_id=new_uuid7(),
        tenant_id=None,
        service_name="receiver-api",
        stage=Stage.RECEIVED,
        event_scope=EventScope.INGRESS_EVENT,
        outcome=Outcome.FAILURE,
    )
    with caplog.at_level(logging.ERROR, logger="dis-audit"):
        assert await PostgresAuditWriter(engine).write(event) is False
    assert any(r.levelno == logging.ERROR and r.name == "dis-audit" for r in caplog.records)
