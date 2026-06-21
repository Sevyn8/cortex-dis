"""``quarantine.quarantined_rows`` - per-row validation failures (read-only here).

Column set mirrors the LIVE table (introspected in 15a plan mode): the failure
story is DENORMALIZED onto the row at write time by the streaming consumer
(``failure_stage``, ``failure_reason``, ``failure_context``, ``mapping_version_id``,
``quarantined_at``, ``status``), so the read needs NO ``audit.events`` join - this
model is the single source for the wire fields. The raw payload is NOT here (only
``gcs_uri`` + ``row_offset`` locate it in GCS); 15a does not read GCS.

Unlike ``identity_mirror`` (RLS-OFF, D41), this table is RLS ON + FORCE with the
single-GUC ``tenant_isolation`` policy, so the per-tenant scope rides
``rls_session``; the explicit ``WHERE tenant_id`` predicate in ``repos/quarantine.py``
is defense-in-depth, not the sole isolation. dis-ui-server never writes this table
(the streaming consumer owns it, status=NEW only, D82); the model is typed read
metadata only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dis_ui_server.db import Base


class QuarantinedRow(Base):
    """One held row failure (faithful read mirror of ``quarantine.quarantined_rows``)."""

    __tablename__ = "quarantined_rows"
    __table_args__ = {"schema": "quarantine"}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    trace_id: Mapped[UUID] = mapped_column(Uuid)
    source_id: Mapped[str] = mapped_column(String(128))
    row_offset: Mapped[int] = mapped_column(Integer)
    # CHECK ck_qr_failure_stage_vocab (6-member subset); translated to the wire
    # taxonomy in schemas/quarantine.py - the single crosswalk, never re-derived.
    failure_stage: Mapped[str] = mapped_column(String(64))
    failure_reason: Mapped[str] = mapped_column(String(256))  # a FailureCode member (D79)
    failure_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mapping_version_id: Mapped[int] = mapped_column(BigInteger)  # NOT NULL on rows (post-lookup only)
    quarantined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # CHECK ck_qr_status_vocab: NEW | RESOLVED | DISMISSED; 11a writes NEW only (D82).
    status: Mapped[str] = mapped_column(String(32))
