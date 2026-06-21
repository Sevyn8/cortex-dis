"""The atomic dual-write (D30): hot upsert + event insert, one transaction per batch.

**Transaction grain** (architecture 4.6, Slice 10 plan §3): the rollback unit is a
per-tenant batch of ≤ ``BATCH_SIZE_ROW_PAIRS`` row-pairs. One chunk carries one
tenant, so batches are chunk-sequential; beta chunks usually fit one transaction.
Each batch opens ONE ``rls_session`` transaction (``SET LOCAL app.tenant_id``
covers both writes, hard rules 1/12) and performs, in order:

1. **Duplicate detect** (a read, never a write gate — architecture 2.3.2): the
   latest prior event row per dedup key among the batch's keys, via the D33
   window's ``DISTINCT ON`` form over ``ix_*_dedup_key``. Deliberately
   all-partition (a prior event may sit in any partition — correction lookback);
   the bounded per-partition index lookup is the accepted beta posture.
   Within-batch repeats of one dedup key are not flagged (read-time dedup still
   collapses them) — recorded limit.
2. **Event insert** (append-only, no UNIQUE, D33/hard rule 7): executemany of the
   model's full column set (``last_updated_at`` left to its DB default).
3. **Hot merge** (column-scoped + event-time-wins, D63/D64): per natural-key
   group of THIS batch — groups SORTED by the COALESCE'd natural-key tuple
   (the deterministic total order that removes the deadlock hazard between
   overlapping batches on autoscaled instances, Part 3 §4) — dispatched by
   projection type (the TWO-PATH comment below; PG validates NOT NULL on the
   INSERT candidate BEFORE arbitration, so event projections cannot ride an
   ON CONFLICT statement at all):
   - EVENT projections (all current production paths): one conditional
     ``UPDATE … WHERE <COALESCE-key> AND (last_source_event_at IS NULL OR
     :incoming >= last_source_event_at)``; rowcount 0 → one READ-ONLY
     existence check → present = older-event no-op, absent = LOUD raise
     (D63: catalogue-before-sales). No INSERT exists on this path.
   - CATALOGUE-COMPLETE projections (future onboarding path): the proven
     atomic ``INSERT … ON CONFLICT (COALESCE list) DO UPDATE … WHERE``;
     arbiter ``uq_sscp_natural_key`` (M-HOTKEY/0004).
   Concurrency-safe under N instances (D58 split) on BOTH paths: the row lock
   + EvalPlanQual re-evaluation of the WHERE against the locked current row
   means an older event never overwrites a newer one in either arrival order;
   ``>=`` so an exact-tie redelivery rewrites identical values (idempotent in
   effect). A missing event_date partition errors loudly (no DEFAULT
   partition exists — introspected); failures roll the batch back,
   either-or-neither.

Either-or-neither holds at the batch grain: a mid-batch failure rolls back that
batch's hot AND event writes; the message is nacked; earlier committed batches
stay and redelivery converges (event re-appends dedup at read, the ``>=`` upsert
re-applies). Transactional idempotency is deliberately NOT the mechanism (D30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from dis_canonical import StoreSkuChangeEvent, StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_core.errors import HotPositionMissingError
from dis_core.ids import new_uuid7
from dis_enrichment import CURRENT_POSITION, enrichment_fields
from dis_mapping import MappingResult
from dis_rls import rls_session
from dis_validation import mapping_produced_columns
from streaming_consumer.envelope import IngressReadyEvent
from streaming_consumer.pipeline.mapping import LoadedMapping, catalogue_staleness_columns
from streaming_consumer.pipeline.normalize import (
    EventRow,
    NaturalKey,
    canonical_row_hash,
    jsonb_param,
)

# Live table names + event-time columns per routed model (introspected schema).
_EVENT_TABLES: dict[type[BaseModel], tuple[str, str]] = {
    StoreSkuSaleEvent: ("canonical.store_sku_sale_events", "source_sale_timestamp"),
    StoreSkuChangeEvent: ("canonical.store_sku_change_events", "source_event_timestamp"),
}

# Columns whose bind values are pre-serialized JSON text (CAST(:p AS JSONB)).
# attribute_staleness_map joins for the catalogue write (Slice 14d); inert for the
# event paths, which never place it in their projection.
_JSONB_COLUMNS = frozenset(
    {"ingest_metadata", "value_before", "value_after", "change_context", "attribute_staleness_map"}
)

# The hot natural-key columns are carried as FIXED params by _hot_params; the
# catalogue projection must exclude them or they would appear twice in the INSERT.
_HOT_NATURAL_KEY_COLS = frozenset({"sku_id", "sku_variant", "sku_lot_batch"})

DuplicateKind = Literal["DUPLICATE_NOOP", "DUPLICATE_OVERWRITTEN"]


@dataclass(frozen=True)
class DuplicateHit:
    """One dedup-key hit, for the D42 audit detail (never a write gate)."""

    source_event_id: str
    prior_trace_id: UUID
    kind: DuplicateKind
    row_hash: str  # the NEW row's canonical hash
    chunk_row_index: int


@dataclass(frozen=True)
class WriteReport:
    """What the dual-write landed for one chunk."""

    event_rows_written: int
    hot_rows_upserted: int
    batches: int
    duplicates: tuple[DuplicateHit, ...]
    written_to_table: str
    # Older-event no-ops on the incomplete path (D64 event-time-wins declines),
    # surfaced for the CANONICAL_WRITTEN audit detail.
    hot_noops: int = 0


def _event_insert_sql(model: type[BaseModel], table: str) -> tuple[str, tuple[str, ...]]:
    """INSERT SQL over the model's full column set (DB-defaulted columns omitted).

    Columns derive from ``model_fields`` (hand-aligned to live, enforced by the
    Slice 3 reconciliation test) so a schema/model change cannot silently leave a
    column behind here.
    """
    columns = tuple(name for name in model.model_fields if name != "last_updated_at")
    placeholders = ", ".join(f"CAST(:{c} AS JSONB)" if c in _JSONB_COLUMNS else f":{c}" for c in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    return sql, columns


def _batches(rows: list[EventRow], size: int) -> list[list[EventRow]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


@dataclass
class _HotGroup:
    """One batch-local natural-key group (column-scoped, event-time-wins)."""

    natural_key: NaturalKey
    projected: dict[str, Any]
    last_source_event_at: datetime
    source_event_id: str
    chunk_row_index: int


def _group_hot(batch: list[EventRow]) -> list[_HotGroup]:
    """Column-scoped merge within the batch: latest event time wins per column."""
    groups: dict[NaturalKey, _HotGroup] = {}
    for row in batch:  # chunk order; ties resolve to the later row
        group = groups.get(row.natural_key)
        if group is None:
            groups[row.natural_key] = _HotGroup(
                natural_key=row.natural_key,
                projected=dict(row.hot_contributions),
                last_source_event_at=row.event_ts,
                source_event_id=row.source_event_id,
                chunk_row_index=row.chunk_row_index,
            )
            continue
        newer = row.event_ts >= group.last_source_event_at
        for column, value in row.hot_contributions.items():
            if newer or column not in group.projected:
                group.projected[column] = value
        if newer:
            group.last_source_event_at = row.event_ts
            group.source_event_id = row.source_event_id
            group.chunk_row_index = row.chunk_row_index
    return list(groups.values())


async def _detect_duplicates(
    conn: AsyncConnection,
    event: IngressReadyEvent,
    loaded: LoadedMapping,
    batch: list[EventRow],
    *,
    table: str,
    event_ts_column: str,
) -> list[DuplicateHit]:
    """The latest prior row per dedup key (the D33 window, DISTINCT ON form).

    Runs BEFORE this batch's insert, inside the same transaction. Compares the
    canonical payload (the mapping's target columns) by the shared row hash:
    equal → DUPLICATE_NOOP (typical redelivery), different → DUPLICATE_OVERWRITTEN
    (a correction). Audit detail only — the insert below is unconditional.
    """
    payload_columns = list(loaded.source.target_columns)
    select_list = ", ".join(["source_event_id", "trace_id", *payload_columns])
    result = await conn.execute(
        text(
            f"SELECT DISTINCT ON (source_event_id) {select_list} "  # noqa: S608 - model-derived identifiers
            f"FROM {table} "
            "WHERE tenant_id = :tenant_id AND store_id = :store_id "
            "AND source_id = :source_id AND source_event_id = ANY(:keys) "
            f"ORDER BY source_event_id, {event_ts_column} DESC, last_updated_at DESC, id DESC"
        ),
        {
            "tenant_id": event.tenant_id,
            "store_id": event.store_id,
            "source_id": event.source_id,
            "keys": [row.source_event_id for row in batch],
        },
    )
    prior_by_key: dict[str, dict[str, Any]] = {}
    for prior in result.mappings():
        prior_by_key[str(prior["source_event_id"])] = dict(prior)
    hits: list[DuplicateHit] = []
    for row in batch:
        prior_row = prior_by_key.get(row.source_event_id)
        if prior_row is None:
            continue
        prior_payload = {column: prior_row[column] for column in payload_columns}
        kind: DuplicateKind = (
            "DUPLICATE_NOOP" if canonical_row_hash(prior_payload) == row.row_hash else "DUPLICATE_OVERWRITTEN"
        )
        hits.append(
            DuplicateHit(
                source_event_id=row.source_event_id,
                prior_trace_id=UUID(str(prior["trace_id"])),
                kind=kind,
                row_hash=row.row_hash,
                chunk_row_index=row.chunk_row_index,
            )
        )
    return hits


# THE COMPLETENESS-GATED TWO-PATH HOT MERGE (REVISED D63, operator-ratified):
# hot-row CREATION is gated by candidate COMPLETENESS, resolved PER MAPPING at
# load (``LoadedMapping.hot_complete``; the partition derived from the live
# NOT NULL + CHECK set lives in pipeline/mapping.py) — not by event type and
# not per row. PostgreSQL validates NOT NULL on the INSERT candidate BEFORE
# conflict arbitration (verified live, role-independent), which is why an
# incomplete mapping can never ride an INSERT ... ON CONFLICT statement.
#
# - COMPLETE mapping (no production mapping today; the future catalogue
#   slice): the proven single INSERT ... ON CONFLICT (COALESCE list)
#   DO UPDATE ... WHERE statement (M-HOTKEY/0004; 3a/3b proven). Creates or
#   updates; the ONLY path that inserts.
#
# - INCOMPLETE mapping (every current production path): ONE conditional UPDATE
#   over the COALESCE'd key with the D64 event-time-wins predicate. rowcount=0
#   → one READ-ONLY existence check → present = older-event no-op (counted,
#   audited); absent = a D63 MISS. The miss does NOT abort the batch
#   transaction: the event rows ALREADY appended in this transaction COMMIT
#   (history retained — revised D63), and write_chunk raises LOUDLY after the
#   commit so the chunk nacks toward quarantine (Slice 11). NO INSERT exists
#   on this path under any concurrency — the create-race cannot occur. The
#   UPDATE's row lock + EvalPlanQual re-evaluation of its WHERE against the
#   locked current row gives the same older-cannot-overwrite guarantee as the
#   DO UPDATE arm (proven by the two-writer tests on this path).
#
# Both paths run inside the per-batch rls_session transaction (D30) and in
# the deterministic sorted-key order (deadlock avoidance).
_HOT_CONFLICT_TARGET = "(tenant_id, store_id, sku_id, COALESCE(sku_variant, ''), COALESCE(sku_lot_batch, ''))"

_HOT_KEY_MATCH = (
    "tenant_id = :tenant_id AND store_id = :store_id AND sku_id = :sku_id "
    "AND COALESCE(sku_variant, '') = COALESCE(CAST(:sku_variant AS VARCHAR), '') "
    "AND COALESCE(sku_lot_batch, '') = COALESCE(CAST(:sku_lot_batch AS VARCHAR), '')"
)

HotMergeOutcome = Literal["written", "noop_older", "missing"]


def hot_sort_key(group: _HotGroup) -> tuple[str, str, str]:
    """The deterministic per-batch upsert order: the COALESCE'd natural-key tuple.

    A total order shared by every instance — overlapping batches then acquire
    hot-row locks in the same sequence, which removes the deadlock hazard
    (proven: opposite-order interleave deadlocks, total-order commits; Part 3 §4).
    """
    sku_id, sku_variant, sku_lot_batch = group.natural_key
    return (sku_id, sku_variant or "", sku_lot_batch or "")


def _hot_params(
    event: IngressReadyEvent,
    loaded: LoadedMapping,
    group: _HotGroup,
    *,
    dis_channel: str,
    tax_treatment: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Shared bind params for both hot paths; returns (params, projected).

    ``tax_treatment`` is the EVENT-path fixed injection (consumer-injected there).
    On the catalogue path it is enrichment-produced and arrives via ``projected``
    (slice-5b, D98) — ``**projected`` overrides this default — so the catalogue caller
    passes nothing; the event path still passes it explicitly."""
    sku_id, sku_variant, sku_lot_batch = group.natural_key
    projected = dict(group.projected)
    params: dict[str, Any] = {
        "id": new_uuid7(),
        "tenant_id": event.tenant_id,
        "store_id": event.store_id,
        "sku_id": sku_id,
        "sku_variant": sku_variant,
        "sku_lot_batch": sku_lot_batch,
        "tax_treatment": tax_treatment,
        "last_source_event_at": group.last_source_event_at,
        "mapping_version_id": loaded.mapping_version_id,
        "trace_id": event.trace_id,
        "dis_channel": dis_channel,
        "ingest_metadata": jsonb_param(
            {
                # Write-shape aligned to the live hot ingest_metadata comment's key
                # vocabulary (execute-time item 5): the hot table has no first-class
                # source_event_id column, so lineage carries it here.
                "source_id": event.source_id,
                "source_event_id": group.source_event_id,
                "source_event_timestamp": group.last_source_event_at.isoformat(),
                "dis_received_timestamp": event.received_ts.isoformat(),
                "csv_row_num": group.chunk_row_index,
            }
        ),
        **projected,
    }
    return params, projected


async def _update_hot_incomplete_path(
    conn: AsyncConnection,
    params: dict[str, Any],
    projected: dict[str, Any],
) -> HotMergeOutcome:
    """The INCOMPLETE-mapping path: update-or-miss. NO INSERT exists here.

    rowcount >= 1 → ``written`` (the event-time-wins update applied).
    rowcount = 0 → ONE READ-ONLY existence check: present → ``noop_older`` (an
    older event lost to a newer row, D64); absent → ``missing`` (a D63 miss —
    the CALLER commits the batch first so the event rows are retained, then
    raises loudly). This function performs no write after rowcount = 0.
    """
    update_columns = [
        *projected.keys(),
        "last_source_event_at",
        "mapping_version_id",
        "trace_id",
        "dis_channel",
        "ingest_metadata",
    ]
    set_list = ", ".join(
        f"{c} = CAST(:{c} AS JSONB)" if c in _JSONB_COLUMNS else f"{c} = :{c}" for c in update_columns
    )
    updated = await conn.execute(
        text(
            "UPDATE canonical.store_sku_current_position "  # noqa: S608 - fixed identifiers
            f"SET {set_list} WHERE {_HOT_KEY_MATCH} "
            "AND (last_source_event_at IS NULL OR :last_source_event_at >= last_source_event_at)"
        ),
        params,
    )
    if updated.rowcount > 0:
        return "written"
    exists = (
        await conn.execute(
            text(
                "SELECT 1 FROM canonical.store_sku_current_position "  # noqa: S608
                f"WHERE {_HOT_KEY_MATCH}"
            ),
            params,
        )
    ).first()
    return "noop_older" if exists is not None else "missing"


async def _insert_on_conflict_hot_complete_path(
    conn: AsyncConnection,
    params: dict[str, Any],
    projected: dict[str, Any],
) -> HotMergeOutcome:
    """The COMPLETE-mapping path: the proven atomic INSERT … ON CONFLICT.

    The only path that inserts (and so the only one where insert-vs-insert is
    real); the candidate satisfies the hot NOT NULL + CHECK shape by the
    load-time classification (``classify_hot_completeness``).
    """
    insert_columns = [
        "id",
        "tenant_id",
        "store_id",
        "sku_id",
        "sku_variant",
        "sku_lot_batch",
        *projected.keys(),
        # tax_treatment exactly once (slice-5b): on the catalogue path it is
        # enrichment-produced and ALREADY in projected; on the (event-driven) complete
        # path it is the fixed store-injected param and NOT in projected. Listing it
        # unconditionally alongside projected would duplicate the column.
        *(() if "tax_treatment" in projected else ("tax_treatment",)),
        "last_source_event_at",
        "mapping_version_id",
        "trace_id",
        "dis_channel",
        "ingest_metadata",
    ]
    placeholders = ", ".join(
        f"CAST(:{c} AS JSONB)" if c in _JSONB_COLUMNS else f":{c}" for c in insert_columns
    )
    update_columns = [
        *projected.keys(),
        "last_source_event_at",
        "mapping_version_id",
        "trace_id",
        "dis_channel",
        "ingest_metadata",
    ]
    # EXCLUDED carries the already-CAST jsonb from the VALUES row, so every
    # column updates uniformly from EXCLUDED.
    set_list = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_columns)
    await conn.execute(
        text(
            "INSERT INTO canonical.store_sku_current_position "  # noqa: S608 - fixed identifiers
            f"({', '.join(insert_columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT {_HOT_CONFLICT_TARGET} "
            f"DO UPDATE SET {set_list} "
            "WHERE store_sku_current_position.last_source_event_at IS NULL "
            "OR EXCLUDED.last_source_event_at >= store_sku_current_position.last_source_event_at"
        ),
        params,
    )
    return "written"


async def _upsert_hot(
    conn: AsyncConnection,
    event: IngressReadyEvent,
    loaded: LoadedMapping,
    group: _HotGroup,
    *,
    dis_channel: str,
    tax_treatment: str | None,
) -> HotMergeOutcome:
    """One column-scoped hot merge (D63 projection, D64 event-time-wins),
    dispatched by the mapping's load-time completeness classification
    (REVISED D63 — the two-path comment above)."""
    params, projected = _hot_params(
        event, loaded, group, dis_channel=dis_channel, tax_treatment=tax_treatment
    )
    if loaded.hot_complete:
        return await _insert_on_conflict_hot_complete_path(conn, params, projected)
    return await _update_hot_incomplete_path(conn, params, projected)


# ---------------------------------------------------------------------------
# Catalogue (snapshot) write — the SIBLING of write_chunk (Slice 14d). It REUSES
# the proven complete-path hot upsert + event-time-wins arbiter and _hot_params,
# but writes NO event table, runs NO dedup, and does NO event projection. The
# event path above is unchanged. Bootstrap-only: it CREATEs the hot row; collision
# arbitration against an existing row is the deferred collision slice's.
# ---------------------------------------------------------------------------


def _catalogue_groups(event: IngressReadyEvent, result: MappingResult) -> list[_HotGroup]:
    """One hot group per snapshot row: identity projection + staleness stamps.

    ``projected`` is the mapping-produced hot columns this row sets MINUS the
    natural key (carried as fixed params), plus ``attribute_staleness_map`` stamped
    for the contendable attributes the row sets (Slice 14d) — value = the snapshot's
    event-time, which is the envelope ``received_ts`` (the only NOT-NULL-safe time;
    the catalogue file has no per-row timestamp and last_source_event_at is
    consumer-injected). It is upload-time, not capture-time; nothing arbitrates on
    it in this slice (bootstrap-only) — the collision slice's inherited limit."""
    # slice-5b: include the enrichment-produced fields (tax_treatment) so the lib's
    # values reach the hot row via ``projected`` (currency is already mapping-produced;
    # the lib overwrote its value in the contribution upstream).
    produced = mapping_produced_columns(StoreSkuCurrentPosition) | frozenset(
        enrichment_fields(CURRENT_POSITION)
    )
    staleness_cols = catalogue_staleness_columns()
    received_iso = event.received_ts.isoformat()
    groups: list[_HotGroup] = []
    for row, chunk_row_index in zip(result.contribution.to_dicts(), result.source_row_indices, strict=True):
        payload: dict[str, Any] = dict(row)
        projected: dict[str, Any] = {
            name: value
            for name, value in payload.items()
            if name in produced and name not in _HOT_NATURAL_KEY_COLS
        }
        stamp = sorted(set(projected) & staleness_cols)
        if stamp:
            projected["attribute_staleness_map"] = jsonb_param({col: received_iso for col in stamp})
        groups.append(
            _HotGroup(
                natural_key=(
                    str(payload["sku_id"]),
                    payload.get("sku_variant"),
                    payload.get("sku_lot_batch"),
                ),
                projected=projected,
                last_source_event_at=event.received_ts,
                source_event_id=f"{event.bronze_ref}:{chunk_row_index}",
                chunk_row_index=chunk_row_index,
            )
        )
    return groups


async def write_catalogue_chunk(
    engine: AsyncEngine,
    event: IngressReadyEvent,
    loaded: LoadedMapping,
    result: MappingResult,
    *,
    dis_channel: str,
    batch_size: int,
) -> WriteReport:
    """Catalogue bootstrap-CREATE: the complete-path hot upsert per ≤batch group.

    No event-table insert, no dedup. Groups are upserted in the same sorted
    natural-key order as write_chunk (deadlock avoidance), each inside the batch's
    rls_session transaction. ``tax_treatment`` AND ``currency`` are enrichment-produced
    (slice-5b, D95/D98): dis-enrichment wrote them into ``result.contribution`` before
    this sink ran, so they arrive via ``projected`` — there is no fixed-param injection
    on this path anymore."""
    groups = _catalogue_groups(event, result)
    batches = [groups[i : i + batch_size] for i in range(0, len(groups), batch_size)]
    hot_written = 0
    for batch in batches:
        async with rls_session(engine, event.tenant_id) as conn:
            for group in sorted(batch, key=hot_sort_key):
                params, projected = _hot_params(event, loaded, group, dis_channel=dis_channel)
                await _insert_on_conflict_hot_complete_path(conn, params, projected)
                hot_written += 1
    return WriteReport(
        event_rows_written=0,
        hot_rows_upserted=hot_written,
        hot_noops=0,
        batches=len(batches),
        duplicates=(),
        written_to_table="canonical.store_sku_current_position",
    )


async def write_chunk(
    engine: AsyncEngine,
    event: IngressReadyEvent,
    loaded: LoadedMapping,
    event_rows: list[EventRow],
    *,
    dis_channel: str,
    tax_treatment: str | None,
    batch_size: int,
) -> WriteReport:
    """Dual-write the chunk in ≤``batch_size`` row-pair batches (the rollback unit)."""
    table, event_ts_column = _EVENT_TABLES[loaded.target_model]
    insert_sql, insert_columns = _event_insert_sql(loaded.target_model, table)
    duplicates: list[DuplicateHit] = []
    hot_written = 0
    hot_noops = 0
    batches = _batches(event_rows, batch_size)
    for batch in batches:
        # Deterministic total order on the COALESCE'd natural-key tuple:
        # overlapping batches on other instances acquire hot-row locks in the
        # same sequence — no lock cycles, no deadlocks (Part 3 §4).
        groups = sorted(_group_hot(batch), key=hot_sort_key)
        misses: list[_HotGroup] = []
        async with rls_session(engine, event.tenant_id) as conn:
            duplicates.extend(
                await _detect_duplicates(
                    conn, event, loaded, batch, table=table, event_ts_column=event_ts_column
                )
            )
            await conn.execute(
                text(insert_sql),
                [{column: row.params.get(column) for column in insert_columns} for row in batch],
            )
            for group in groups:
                outcome = await _upsert_hot(
                    conn, event, loaded, group, dis_channel=dis_channel, tax_treatment=tax_treatment
                )
                if outcome == "written":
                    hot_written += 1
                elif outcome == "noop_older":
                    hot_noops += 1
                else:
                    misses.append(group)
        # The batch transaction has COMMITTED here: the event rows (history)
        # and every successful hot merge are retained (REVISED D63). A miss
        # then raises LOUDLY so the chunk nacks toward quarantine (Slice 11);
        # redelivery re-appends events (read-time dedup absorbs) and retries
        # the merge once catalogue/position has onboarded.
        if misses:
            keys = sorted(str(m.natural_key) for m in misses)[:20]
            # A dedicated class (Slice 30b) so the FAILURE audit maps to the stable
            # FailureCode.HOT_POSITION_MISSING instead of the INFRA_FAILURE bucket.
            raise HotPositionMissingError(
                f"{len(misses)} first-seen SKU(s) on an INCOMPLETE-mapping chunk: no "
                "store_sku_current_position row exists and the projection cannot create "
                "one (REVISED D63: completeness-gated creation; event history is "
                "RETAINED, the hot merge waits for catalogue/position). "
                f"natural keys (first 20): {keys} "
                f"[tenant_id={event.tenant_id} trace_id={event.trace_id} "
                f"mapping_version_id={loaded.mapping_version_id}]",
                tenant_id=str(event.tenant_id),
                trace_id=str(event.trace_id),
                mapping_version_id=loaded.mapping_version_id,
                miss_count=len(misses),
            )
    return WriteReport(
        event_rows_written=len(event_rows),
        hot_rows_upserted=hot_written,
        hot_noops=hot_noops,
        batches=len(batches),
        duplicates=tuple(duplicates),
        written_to_table=table,
    )
