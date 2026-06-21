"""dis-canonical — Pydantic models for the canonical schema.

One model per ``canonical.*`` base table, hand-aligned to the live ithina_dis_db
schema (not the DDL files). The in-memory representation only; SQL conversion is
the consumer's DB layer (no SQLAlchemy / ORM here).

- ``StoreSkuCurrentPosition`` — hot table (current state, merge upsert).
- ``StoreSkuSaleEvent`` / ``StoreSkuChangeEvent`` — append-only event tables.
- ``StoreSkuSignalHistory`` — daily-compute signal history (no mapping_version_id).
"""

from __future__ import annotations

from dis_canonical.events import StoreSkuChangeEvent, StoreSkuSaleEvent
from dis_canonical.hot import StoreSkuCurrentPosition
from dis_canonical.signals import StoreSkuSignalHistory

__all__ = [
    "StoreSkuChangeEvent",
    "StoreSkuCurrentPosition",
    "StoreSkuSaleEvent",
    "StoreSkuSignalHistory",
]
