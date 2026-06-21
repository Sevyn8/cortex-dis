"""``config.source_mappings`` — the mapping-template version row (live-introspected 14b).

Column set, constraints, and defaults mirror the LIVE table (Slice 14a,
Alembic 0005; introspected in 14b plan mode): 16 columns, RLS ON+FORCE with the
single-GUC ``tenant_isolation`` policy (D69), template grain
``(tenant_id, source_id, template_id)`` (D68). This model is SHAPE only — no DDL
is ever emitted from it (14a settled the schema; ``Base.metadata.create_all`` is
never called in this service).

Two deliberate mapping quirks, both load-bearing:

- ``metadata`` is a real column but a reserved Declarative attribute, so the
  Python attribute is ``metadata_`` mapped onto the ``"metadata"`` column.
- ``version_seq_per_source`` is NOT NULL but trigger-assigned
  (``trg_csm_set_version_seq`` fills it when NULL/0 — BEFORE triggers run ahead
  of the NOT NULL check). The write path therefore OMITS it from INSERT values
  and reads it back via ``RETURNING``; it carries no Python default so an
  accidental client-side value cannot suppress the trigger.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, SmallInteger, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dis_ui_server.db import Base


class SourceMappingRow(Base):
    """One version row of one mapping template (lineage keyed by ``template_id``)."""

    __tablename__ = "source_mappings"
    __table_args__ = {"schema": "config"}

    # Surrogate key: global BIGSERIAL — the D22 canonical-row pin / audit reference.
    mapping_version_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Identity (the D68 grain) — tenant_id is also the RLS policy column.
    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    source_id: Mapped[str] = mapped_column(String(128))
    template_id: Mapped[UUID] = mapped_column(Uuid)
    template_name: Mapped[str] = mapped_column(Text)
    # Trigger-assigned (see module docstring); never sent on INSERT.
    version_seq_per_source: Mapped[int] = mapped_column(SmallInteger)

    # Lifecycle (D17 vocabulary; CHECK ck_csm_status_vocab).
    status: Mapped[str] = mapped_column(Text)

    # Packet axis (Slice 14d): snapshot | sales | inventory_change. Stored, not
    # inferred; the vocabulary is code-enforced (dis_validation.TEMPLATE_TYPES),
    # no DB enum/CHECK. Set at creation; lineage-fixed (carried onto chained DRAFTs).
    template_type: Mapped[str] = mapped_column(Text)

    # Mapping payload (D49 shape; validated by dis-mapping SourceMapping before any write).
    mapping_rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    pre_validation_suite_ref: Mapped[str | None] = mapped_column(String(256))
    post_validation_suite_ref: Mapped[str | None] = mapped_column(String(256))

    # Lineage chain (informational — no FK on the live table; the write path owns it).
    predecessor_version_id: Mapped[int | None] = mapped_column(BigInteger)

    # Lifecycle timestamps (status-consistency CHECKs live in the DB).
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Authorship (nullable pending the CM claim sign-off, D56 / contract Blocker 5).
    created_by_user_id: Mapped[UUID | None] = mapped_column(Uuid)

    # DIS-managed.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
