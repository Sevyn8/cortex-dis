"""The completeness-gated TWO-PATH hot merge under TWO real concurrent writers
(D58 split, REVISED D63).

Every test runs the PRODUCTION dispatcher (``sinks.canonical._upsert_hot``)
under the SERVICE role (``ithina_dis_user``) with RLS active, on two separate
engines, READ COMMITTED — the hold-and-collide harness proven in plan Part 3:
writer A executes and HOLDS its transaction; writer B executes the same-key
merge and must BLOCK on A's lock; A commits; B's outcome is then asserted.

- ``test_concurrent_insert_takes_update_branch`` (3a, CATALOGUE path — the only
  path that inserts): two instances upsert the same NEW SKU concurrently →
  exactly one row; the loser takes the UPDATE branch — NO unique violation
  surfaces (the churn the read-modify-write design had).
- ``test_concurrent_older_event_cannot_overwrite`` (3b, CATALOGUE path) and
  ``test_concurrent_older_event_cannot_overwrite_event_path`` (3b, EVENT
  path): same EXISTING SKU, one writer carrying a NEWER source-event-time
  (holds), one an OLDER (collides). The WHERE predicate — the ``DO UPDATE``
  arm's on one path, the conditional UPDATE's on the other — is re-evaluated
  via EvalPlanQual against the LOCKED current row, so the older event takes
  the lock, re-evaluates, and DECLINES — it can NEVER overwrite the newer,
  regardless of arrival order (the reverse order is the trivial sequential
  case, asserted too).
- ``test_completeness_dispatch_and_missing_outcome``: the dispatch is the
  LOAD-TIME flag (REVISED D63: completeness-gated, per mapping); the
  incomplete path's unseen-SKU outcome is a READ-ONLY ``missing`` (no INSERT,
  no row) — the loud raise happens in write_chunk AFTER the batch commits, so
  event history is retained (the pipeline-level proof lives in
  test_dual_write).

The complete-path tests use a catalogue-COMPLETE projection with a fabricated
``hot_complete=True`` (no production mapping classifies complete today — the
INSERT candidate must satisfy the hot NOT NULL columns, which PG validates
BEFORE arbitration); the incomplete-path tests use a sale-style projection,
the production shape.

Load-bearing, ERROR-not-skip: missing env raises StackRequiredError (conftest);
there is no pytest.skip on any path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from dis_canonical import StoreSkuSaleEvent
from dis_core.ids import new_uuid7
from dis_mapping import SourceMapping
from dis_rls import create_rls_engine, rls_session
from dis_testing.fixtures import PRIMARY_STORE, PRIMARY_TENANT
from streaming_consumer.envelope import IngressReadyEvent
from streaming_consumer.pipeline.mapping import LoadedMapping
from streaming_consumer.sinks.canonical import _HotGroup, _upsert_hot  # noqa: PLC2701 - white-box

from .conftest import Cleanup

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mappings"

_T0 = datetime(2026, 6, 5, 10, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(hours=1)
_T2 = _T0 + timedelta(hours=2)


def _event() -> IngressReadyEvent:
    trace = new_uuid7()
    return IngressReadyEvent(
        schema_version=1,
        trace_id=trace,
        tenant_id=PRIMARY_TENANT.uuid,
        store_id=PRIMARY_STORE.uuid,
        source_id="sc_pos_v1",
        template_id=new_uuid7(),  # immaterial: this test fabricates LoadedMapping, no lookup runs
        bronze_ref=new_uuid7(),
        gcs_uri=(
            f"gs://test/tenant/{PRIMARY_TENANT.uuid}/source/sc_pos_v1/yyyy=2026/mm=06/dd=05/{trace}.csv"
        ),
        received_ts=_T0,
    )


def _loaded(consumer_mappings: dict[str, int], *, complete: bool) -> LoadedMapping:
    """A LoadedMapping with the completeness flag FABRICATED per test path.

    No production mapping classifies complete today (the registries cannot
    yield the discriminating five), so the COMPLETE-path tests set the flag
    directly — exactly what a future catalogue mapping's load-time
    classification will produce.
    """
    rules = json.loads((_FIXTURES / "sale_pos_v1.json").read_text())
    return LoadedMapping(
        mapping_version_id=consumer_mappings["sc_pos_v1"],
        source=SourceMapping.model_validate(rules),
        target_model=StoreSkuSaleEvent,
        hot_complete=complete,
    )


def _group(sku: str, *, ts: datetime, price: Decimal) -> _HotGroup:
    # COMPLETE-shape projection: the only projection class whose INSERT
    # candidate satisfies the hot NOT NULL columns. Used with
    # hot_complete=True (the dispatch is the LOAD-TIME mapping flag).
    return _HotGroup(
        natural_key=(sku, None, None),
        projected={
            "product_name": "Race Widget",
            "product_category": "Hardware",
            "current_retail_price": price,
            "unit_cost": Decimal("0.5000"),
            "currency": "USD",
        },
        last_source_event_at=ts,
        source_event_id=f"race:{ts.isoformat()}",
        chunk_row_index=0,
    )


def _event_group(sku: str, *, ts: datetime, price: Decimal) -> _HotGroup:
    # Sale-style projection (the production shape). Used with
    # hot_complete=False -> the conditional-UPDATE incomplete path.
    return _HotGroup(
        natural_key=(sku, None, None),
        projected={"current_retail_price": price, "currency": "USD"},
        last_source_event_at=ts,
        source_event_id=f"race-evt:{ts.isoformat()}",
        chunk_row_index=0,
    )


_GroupFactory = Callable[..., _HotGroup]


async def _race(
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
    sku: str,
    *,
    a_ts: datetime,
    a_price: Decimal,
    b_ts: datetime,
    b_price: Decimal,
    group_factory: _GroupFactory = _group,
    complete: bool = True,
) -> dict[str, Any]:
    """A merges and HOLDS; B merges the same key (must block); A commits; B commits."""
    engine_a = create_rls_engine(stack_env["POSTGRES_URL"])
    engine_b = create_rls_engine(stack_env["POSTGRES_URL"])
    loaded = _loaded(consumer_mappings, complete=complete)
    a_done = asyncio.Event()
    a_may_commit = asyncio.Event()
    result: dict[str, Any] = {}

    async def writer_a() -> None:
        async with rls_session(engine_a, PRIMARY_TENANT.uuid) as conn:
            await _upsert_hot(
                conn,
                _event(),
                loaded,
                group_factory(sku, ts=a_ts, price=a_price),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
            a_done.set()
            await a_may_commit.wait()  # hold the lock while B collides

    async def writer_b() -> None:
        await a_done.wait()
        async with rls_session(engine_b, PRIMARY_TENANT.uuid) as conn:
            task = asyncio.get_event_loop().create_task(
                _upsert_hot(
                    conn,
                    _event(),
                    loaded,
                    group_factory(sku, ts=b_ts, price=b_price),
                    dis_channel="csv_upload",
                    tax_treatment="EXCLUSIVE",
                )
            )
            await asyncio.sleep(0.8)
            result["b_blocked"] = not task.done()
            a_may_commit.set()  # A commits; B's statement resolves against the locked row
            try:
                await task
                result["b_outcome"] = "ok"
            except Exception as exc:  # noqa: BLE001 - the assertion target
                result["b_outcome"] = f"raised:{type(exc).__name__}"

    try:
        await asyncio.gather(writer_a(), writer_b())
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    return result


def _final_row(dis_admin: Engine, sku: str) -> tuple[int, Decimal, datetime]:
    with dis_admin.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT current_retail_price, last_source_event_at "
                "FROM canonical.store_sku_current_position "
                "WHERE tenant_id = CAST(:t AS uuid) AND sku_id = :sku"
            ),
            {"t": str(PRIMARY_TENANT.uuid), "sku": sku},
        ).all()
    assert rows, f"no hot row for {sku}"
    return len(rows), Decimal(str(rows[0].current_retail_price)), rows[0].last_source_event_at


async def test_concurrent_insert_takes_update_branch(
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
    dis_admin: Engine,
    cleanup: Cleanup,
) -> None:
    sku = f"RACE3A-{new_uuid7().hex[:10]}"
    cleanup.skus.append(sku)
    result = await _race(
        stack_env,
        consumer_mappings,
        sku,
        a_ts=_T1,
        a_price=Decimal("1.0000"),
        b_ts=_T2,
        b_price=Decimal("2.0000"),
    )
    # B genuinely collided on A's in-flight insert, then took the UPDATE branch:
    # no unique violation surfaced to the app, exactly one row, B's newer values.
    assert result["b_blocked"] is True
    assert result["b_outcome"] == "ok"
    count, price, ts = _final_row(dis_admin, sku)
    assert count == 1
    assert price == Decimal("2.0000")
    assert ts == _T2


async def test_concurrent_older_event_cannot_overwrite(
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
    dis_admin: Engine,
    cleanup: Cleanup,
) -> None:
    sku = f"RACE3B-{new_uuid7().hex[:10]}"
    cleanup.skus.append(sku)

    # Seed the existing row at T0 (committed, via the production statement).
    engine = create_rls_engine(stack_env["POSTGRES_URL"])
    loaded = _loaded(consumer_mappings, complete=True)
    try:
        async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
            await _upsert_hot(
                conn,
                _event(),
                loaded,
                _group(sku, ts=_T0, price=Decimal("0.5000")),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
    finally:
        await engine.dispose()

    # The race: NEWER (T2) holds the row lock; OLDER (T1) collides. The DO UPDATE
    # WHERE predicate evaluates against the LOCKED current row (T2 after A's
    # commit), so the older writer declines — no overwrite, in this arrival order.
    result = await _race(
        stack_env,
        consumer_mappings,
        sku,
        a_ts=_T2,
        a_price=Decimal("9.0000"),
        b_ts=_T1,
        b_price=Decimal("5.0000"),
    )
    assert result["b_blocked"] is True
    assert result["b_outcome"] == "ok"  # no error; it simply updated nothing
    count, price, ts = _final_row(dis_admin, sku)
    assert count == 1
    assert price == Decimal("9.0000")  # the newer event's value held
    assert ts == _T2

    # The reverse arrival order is the sequential case: an older upsert against
    # the committed newer row also declines...
    engine = create_rls_engine(stack_env["POSTGRES_URL"])
    try:
        async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
            await _upsert_hot(
                conn,
                _event(),
                loaded,
                _group(sku, ts=_T1, price=Decimal("4.0000")),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
        count, price, ts = _final_row(dis_admin, sku)
        assert (count, price, ts) == (1, Decimal("9.0000"), _T2)
        # ...and a strictly newer one updates (event-time-wins, both directions).
        async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
            await _upsert_hot(
                conn,
                _event(),
                loaded,
                _group(sku, ts=_T2 + timedelta(hours=1), price=Decimal("11.0000")),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
        count, price, ts = _final_row(dis_admin, sku)
        assert (count, price) == (1, Decimal("11.0000"))
    finally:
        await engine.dispose()


async def test_concurrent_older_event_cannot_overwrite_event_path(
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
    dis_admin: Engine,
    cleanup: Cleanup,
) -> None:
    """3b on the INCOMPLETE (production) path: the conditional UPDATE's WHERE is
    re-evaluated via EvalPlanQual against the LOCKED current row — same
    guarantee as the DO UPDATE arm, proven with two real writers."""
    sku = f"RACE3BE-{new_uuid7().hex[:10]}"
    cleanup.skus.append(sku)

    # Seed the existing row at T0 via the complete path (the only creator).
    engine = create_rls_engine(stack_env["POSTGRES_URL"])
    try:
        async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
            await _upsert_hot(
                conn,
                _event(),
                _loaded(consumer_mappings, complete=True),
                _group(sku, ts=_T0, price=Decimal("0.5000")),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
    finally:
        await engine.dispose()

    # NEWER (T2) holds; OLDER (T1) collides — both via the INCOMPLETE path's
    # conditional UPDATE (sale-style event projection, the production shape).
    result = await _race(
        stack_env,
        consumer_mappings,
        sku,
        a_ts=_T2,
        a_price=Decimal("9.0000"),
        b_ts=_T1,
        b_price=Decimal("5.0000"),
        group_factory=_event_group,
        complete=False,
    )
    assert result["b_blocked"] is True
    assert result["b_outcome"] == "ok"  # declined quietly: rowcount 0, no write
    count, price, ts = _final_row(dis_admin, sku)
    assert count == 1
    assert price == Decimal("9.0000")  # the newer event's value held
    assert ts == _T2

    # Reverse arrival (sequential): older declines, strictly newer updates.
    engine = create_rls_engine(stack_env["POSTGRES_URL"])
    loaded_incomplete = _loaded(consumer_mappings, complete=False)
    try:
        async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
            outcome_old = await _upsert_hot(
                conn,
                _event(),
                loaded_incomplete,
                _event_group(sku, ts=_T1, price=Decimal("4.0000")),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
        assert outcome_old == "noop_older"
        count, price, ts = _final_row(dis_admin, sku)
        assert (count, price, ts) == (1, Decimal("9.0000"), _T2)
        async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
            outcome_new = await _upsert_hot(
                conn,
                _event(),
                loaded_incomplete,
                _event_group(sku, ts=_T2 + timedelta(hours=1), price=Decimal("11.0000")),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
        assert outcome_new == "written"
        count, price, ts = _final_row(dis_admin, sku)
        assert (count, price) == (1, Decimal("11.0000"))
    finally:
        await engine.dispose()


async def test_completeness_dispatch_and_missing_outcome(
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
    dis_admin: Engine,
    cleanup: Cleanup,
) -> None:
    """The dispatch is the LOAD-TIME flag (REVISED D63), and the incomplete
    path's unseen-SKU outcome is a read-only ``missing`` — NO INSERT, no row,
    no raise at the merge level (write_chunk commits the batch and raises)."""
    sku = f"DSP-{new_uuid7().hex[:10]}"
    cleanup.skus.append(sku)
    engine = create_rls_engine(stack_env["POSTGRES_URL"])
    try:
        # Incomplete mapping + unseen SKU -> 'missing', zero rows (no INSERT path).
        async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
            outcome = await _upsert_hot(
                conn,
                _event(),
                _loaded(consumer_mappings, complete=False),
                _event_group(sku, ts=_T1, price=Decimal("1.0000")),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
        assert outcome == "missing"
        with dis_admin.begin() as conn_check:
            count = conn_check.execute(
                text(
                    "SELECT COUNT(*) FROM canonical.store_sku_current_position "
                    "WHERE tenant_id = CAST(:t AS uuid) AND sku_id = :sku"
                ),
                {"t": str(PRIMARY_TENANT.uuid), "sku": sku},
            ).scalar_one()
        assert count == 0

        # Complete mapping + the same unseen SKU -> the ON CONFLICT path CREATES.
        async with rls_session(engine, PRIMARY_TENANT.uuid) as conn:
            outcome = await _upsert_hot(
                conn,
                _event(),
                _loaded(consumer_mappings, complete=True),
                _group(sku, ts=_T1, price=Decimal("1.0000")),
                dis_channel="csv_upload",
                tax_treatment="EXCLUSIVE",
            )
        assert outcome == "written"
        count, price, _ts = _final_row(dis_admin, sku)
        assert (count, price) == (1, Decimal("1.0000"))
    finally:
        await engine.dispose()
