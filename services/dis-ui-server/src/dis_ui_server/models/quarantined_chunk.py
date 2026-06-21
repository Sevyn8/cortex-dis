"""``quarantine.quarantined_chunks`` - whole-chunk rejections (read-only here).

The chunk-grain sibling of :class:`QuarantinedRow`: a whole file/chunk rejected
before per-row work (e.g. ``MAPPING_CONFIG_INVALID``, a post-fetch contract
violation, a row-less gate failure). Highest-value quarantine content - a rejected
upload that would otherwise be invisible to the tenant - so 15a's list/detail union
both tables. Same denormalized failure story as the row table; two columns differ:
no ``row_offset`` (the chunk has no single row) and ``mapping_version_id`` is
NULLABLE (a pre-lookup chunk failure carries none → the "v1" header is absent).

RLS ON + FORCE, single-GUC ``tenant_isolation`` (read rides ``rls_session``).
Read-only; the streaming consumer owns writes (status=NEW only, D82).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dis_ui_server.db import Base


class QuarantinedChunk(Base):
    """One held chunk failure (faithful read mirror of ``quarantine.quarantined_chunks``)."""

    __tablename__ = "quarantined_chunks"
    __table_args__ = {"schema": "quarantine"}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    trace_id: Mapped[UUID] = mapped_column(Uuid)
    source_id: Mapped[str] = mapped_column(String(128))
    # CHECK ck_qc_failure_stage_vocab (9-member superset, incl. pre-lookup stages);
    # translated via the single crosswalk in schemas/quarantine.py.
    failure_stage: Mapped[str] = mapped_column(String(64))
    failure_reason: Mapped[str] = mapped_column(String(256))  # a FailureCode member (D79)
    failure_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mapping_version_id: Mapped[int | None] = mapped_column(BigInteger)  # NULL for pre-lookup failures
    quarantined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # CHECK ck_qc_status_vocab: NEW | RESOLVED | DISMISSED; 11a writes NEW only (D82).
    status: Mapped[str] = mapped_column(String(32))
