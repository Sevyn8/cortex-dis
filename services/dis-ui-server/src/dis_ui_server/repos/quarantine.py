"""``quarantine.*`` reads - the quarantine console's data access (slice 15a).

Both ``quarantined_rows`` and ``quarantined_chunks`` are RLS ON + FORCE with the
single-GUC ``tenant_isolation`` policy, so the per-tenant scope is the DATABASE's
guarantee, applied by ``rls_session(engine, tenant_id)``. The explicit ``WHERE
tenant_id`` predicate on every statement here is defense-in-depth (the 14b D41
pattern), not the sole isolation - but it is cheap and the tenant-A/tenant-B test
pins it either way. Reads execute CORE-STYLE on the ``rls_session`` connection
(service CLAUDE.md durable invariant); never an ``AsyncSession``.

This module speaks DB vocabulary only - wire<->DB translation (stage/status
crosswalk, the type-tagged id, window->cutoff) lives in the handler. The list is a
UNION of the two tables, each leg tagged with its ``kind`` and merged newest-first.
``tenant_id`` MUST come from the verified token (``tenant_uuid_of``); this module
trusts its caller on that - the auth seam is the only producer.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Select, func, literal, select
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_core.errors import ResourceNotFoundError, TenantScopeError
from dis_ui_server.auth.scope import ReadScope
from dis_ui_server.db import read_session
from dis_ui_server.models import QuarantinedChunk, QuarantinedRow

# The list-row projection, identical on both tables so the two legs union cleanly.
# (failure_context is detail-only; it is not read for the list.)
_LIST_COLUMNS = ("id", "trace_id", "source_id", "failure_stage", "failure_reason", "quarantined_at", "status")
# The detail projection adds failure_context (Context composition) and mapping_version_id.
_DETAIL_COLUMNS = (*_LIST_COLUMNS, "failure_context", "mapping_version_id")


def _tenant_term(
    model: type[QuarantinedRow] | type[QuarantinedChunk], scope: ReadScope
) -> list[ColumnElement[bool]]:
    """The in-query tenant predicate (Slice 17b): applied for a pinned (TENANT) scope,
    OMITTED for PLATFORM see-all (the RLS USING branch is the see-all isolation).

    Conditioned on ``scope.is_platform``, NEVER on ``tenant_id`` being absent — so a
    TENANT scope ALWAYS carries the predicate (the catastrophe invariant; criterion 7
    pins it). These tables are RLS ON+FORCE, so the predicate is defense-in-depth for
    TENANT; for PLATFORM the policy widens reads and the predicate must not re-pin.
    """
    if scope.is_platform:
        return []
    if scope.tenant_id is None:  # unreachable: a pinned scope always carries a UUID
        raise TenantScopeError("a pinned read scope carries no tenant", tenant_id=None)
    return [model.tenant_id == scope.tenant_id]


def _list_filters(
    model: type[QuarantinedRow] | type[QuarantinedChunk],
    scope: ReadScope,
    *,
    source: str | None,
    stages: list[str] | None,
    statuses: list[str] | None,
    cutoff: datetime | None,
) -> list[ColumnElement[bool]]:
    """The WHERE terms shared by both union legs (tenant predicate first, when pinned)."""
    terms: list[ColumnElement[bool]] = _tenant_term(model, scope)
    if source is not None:
        terms.append(model.source_id == source)
    if stages is not None:
        terms.append(model.failure_stage.in_(stages))
    if statuses is not None:
        terms.append(model.status.in_(statuses))
    if cutoff is not None:
        terms.append(model.quarantined_at >= cutoff)
    return terms


def _leg(
    model: type[QuarantinedRow] | type[QuarantinedChunk],
    kind: str,
    terms: list[ColumnElement[bool]],
) -> Select[Any]:
    columns = [getattr(model, name) for name in _LIST_COLUMNS]
    return select(*columns, literal(kind).label("kind")).where(*terms)


async def list_held_items(
    engine: AsyncEngine,
    scope: ReadScope,
    *,
    source: str | None = None,
    stages: list[str] | None = None,
    statuses: list[str] | None = None,
    cutoff: datetime | None = None,
) -> Sequence[Row[Any]]:
    """The tenant's held items (rows ∪ chunks), filtered, newest first.

    Filters are DB-vocabulary already (the handler translated wire -> DB). An empty
    ``stages``/``statuses`` list means "this wire bucket maps to no DB value" -> the
    ``IN ()`` matches nothing, which is the honest result (e.g. a bucket with no
    producing path), never "no filter".
    """
    row_terms = _list_filters(
        QuarantinedRow, scope, source=source, stages=stages, statuses=statuses, cutoff=cutoff
    )
    chunk_terms = _list_filters(
        QuarantinedChunk, scope, source=source, stages=stages, statuses=statuses, cutoff=cutoff
    )
    row_stmt = _leg(QuarantinedRow, "row", row_terms)
    chunk_stmt = _leg(QuarantinedChunk, "chunk", chunk_terms)
    async with read_session(engine, is_platform=scope.is_platform, tenant_id=scope.tenant_id) as conn:
        rows = list((await conn.execute(row_stmt)).all())
        rows += list((await conn.execute(chunk_stmt)).all())
    # Newest first; id as the stable tie-breaker (UUIDv7 is time-ordered).
    rows.sort(key=lambda r: (r.quarantined_at, r.id), reverse=True)
    return rows


async def count_open(engine: AsyncEngine, scope: ReadScope) -> int:
    """The header badge: count of OPEN (status=NEW) held items, FILTER-INDEPENDENT.

    Deliberately ignores every list filter - it is the tenant's total open count (or the
    cross-tenant total under a PLATFORM see-all scope). Counts both tables.
    """

    def _open_count(model: type[QuarantinedRow] | type[QuarantinedChunk]) -> Select[Any]:
        return (
            select(func.count()).select_from(model).where(*_tenant_term(model, scope), model.status == "NEW")
        )

    async with read_session(engine, is_platform=scope.is_platform, tenant_id=scope.tenant_id) as conn:
        rows_open = (await conn.execute(_open_count(QuarantinedRow))).scalar_one()
        chunks_open = (await conn.execute(_open_count(QuarantinedChunk))).scalar_one()
    return int(rows_open) + int(chunks_open)


async def get_held_item(engine: AsyncEngine, scope: ReadScope, *, kind: str, item_id: UUID) -> Row[Any]:
    """One held item by ``kind`` + PK, or a clean 404.

    The ``kind`` tag dispatches to exactly one table (no cross-table id ambiguity).
    An unknown id and a cross-tenant id are indistinguishable here - RLS hides the
    other tenant's row, so both surface as ``ResourceNotFoundError`` (404), the
    desired no-existence-oracle behaviour. ``item_id`` is the caller's own opaque
    handle, safe to echo in the error.
    """
    model: type[QuarantinedRow] | type[QuarantinedChunk] = (
        QuarantinedRow if kind == "row" else QuarantinedChunk
    )
    columns = [getattr(model, name) for name in _DETAIL_COLUMNS]
    statement = select(*columns, literal(kind).label("kind")).where(
        model.id == item_id, *_tenant_term(model, scope)
    )
    async with read_session(engine, is_platform=scope.is_platform, tenant_id=scope.tenant_id) as conn:
        result = (await conn.execute(statement)).all()
    if not result:
        raise ResourceNotFoundError(
            f"no quarantined item {kind}:{item_id} for the caller's scope",
            resource="quarantine_item",
            identifier=f"{kind}:{item_id}",
            tenant_id=str(scope.tenant_id) if scope.tenant_id is not None else None,
        )
    return result[0]


__all__ = ["count_open", "get_held_item", "list_held_items"]
