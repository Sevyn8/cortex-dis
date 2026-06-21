"""The audit writer interface and Phase-1 backend selection.

The interface stays stable across the Phase-3 BigQuery addition (``decisions.md`` D34):
both backends satisfy :class:`AuditWriter`, so consumers depend on the protocol, not a
concrete class. Phase 1 selects the Cloud SQL backend; the BigQuery backend is an inert
placeholder seam (see :mod:`dis_audit.bigquery_writer`).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncEngine

from dis_audit.bigquery_writer import BigQueryAuditWriter
from dis_audit.event import AuditEvent
from dis_audit.postgres_writer import PostgresAuditWriter
from dis_core.bq import BqClient
from dis_core.errors import AuditWriteError


@runtime_checkable
class AuditWriter(Protocol):
    """Lands one audit event, fire-and-forget. Returns ``True`` on a confirmed write.

    Implementations never raise to the caller and never block the data path (hard
    rule 11): a failure is logged with context and reported as ``False``.
    """

    async def write(self, event: AuditEvent) -> bool: ...


class AuditBackend(StrEnum):
    """The audit write backends. Phase 1: ``POSTGRES`` active; ``BIGQUERY`` inert (D34)."""

    POSTGRES = "POSTGRES"
    BIGQUERY = "BIGQUERY"


def select_writer(
    backend: AuditBackend,
    *,
    engine: AsyncEngine | None = None,
    bq_client: BqClient | None = None,
) -> AuditWriter:
    """Return the writer for ``backend``.

    Phase 1 uses ``AuditBackend.POSTGRES`` (requires a caller-owned ``engine``). The
    ``BIGQUERY`` branch returns the inert Phase-3 seam, marking the implementation point
    without contacting BigQuery. No silent fallback for a required value (rule 4): a
    missing engine for the Postgres backend raises.
    """
    if backend is AuditBackend.POSTGRES:
        if engine is None:
            raise AuditWriteError("AuditBackend.POSTGRES requires a caller-owned engine")
        return PostgresAuditWriter(engine)
    return BigQueryAuditWriter(bq_client)
