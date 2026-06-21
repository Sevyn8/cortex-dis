"""``identity_mirror.stores`` — the Customer Master store mirror (read-only here).

Column set mirrors the LIVE table (introspected in 14b plan mode): 13 columns,
PK ``(tenant_id, store_id)``, ``store_code`` nullable (faithful copy of CM's
nullable source column, D55). The table is RLS-OFF (D41), so EVERY read of this
model MUST carry an explicit ``tenant_id`` predicate — the in-query scoping is
the only isolation (the registered 14b weak link). That predicate lives in ONE
place, ``repos/stores.py``; do not query this model anywhere else.

dis-ui-server never writes this table (Mirror Sync owns it); the model is typed
read metadata only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from dis_ui_server.db import Base


class StoreRow(Base):
    """One mirrored store row (faithful copy of Customer Master ``core.stores``)."""

    __tablename__ = "stores"
    __table_args__ = {"schema": "identity_mirror"}

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    store_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # CHECK ck_ims_status_vocab: OPENING | ACTIVE | INACTIVE | CLOSED.
    status: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(CHAR(3))
    # CHECK ck_ims_tax_treatment_vocab: INCLUSIVE | EXCLUSIVE.
    tax_treatment: Mapped[str] = mapped_column(Text)
    pc_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pc_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pc_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mirror_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Nullable at source (D55); the wire shape types it nullable too.
    store_code: Mapped[str | None] = mapped_column(Text)
