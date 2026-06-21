"""Write-shape normalization: contribution rows → write-ready event rows.

The engine's contribution carries the mapping-produced columns only; this stage
injects everything consumer-owned (the provenance.py line):

- identity (``tenant_id``/``store_id``), ``trace_id`` (read, never minted),
  ``mapping_version_id`` (D22, hard rule 5), ``dis_channel`` (the bronze row's),
- the D33 dedup key: ``source_id`` (the verified envelope value) and
  ``source_event_id`` — sale rows use ``transaction_id || ':' || line_item_seq``
  when the source supplied BOTH; otherwise (and always for change events) the
  deterministic fallback ``bronze_ref || ':' || chunk_row_index`` (D65:
  redelivery-stable — same bronze object, same key — but NOT
  correction-collapsing: a re-uploaded correction is a new bronze object),
- ``ingest_metadata`` lineage (keys within the live column comments' vocabulary),
- change-event typed shortcuts (``numeric_value_before/after``, ``numeric_change``
  for INVENTORY/PRICE/COST — the live column comments name the consumer as their
  populator),
- sale ``tax_treatment`` denormalized from the store row (live comment:
  "Denormalized from store").

Each row also carries its **hot contribution** (the D63 projection): which hot
columns this row asserts, with values. Grouping per natural key (column-scoped,
event-time-wins within the group) happens in the SINK per batch — the batch is
the rollback unit (D30 at batch grain), so the hot merge must be batch-local.
An unseen SKU's INSERT arm will violate the hot table's NOT NULL catalogue
columns and fail the batch loudly (D63: catalogue-before-sales).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import orjson
from pydantic import BaseModel

from dis_canonical import StoreSkuSaleEvent
from dis_core.ids import new_uuid7
from dis_mapping import MappingResult
from streaming_consumer.envelope import IngressReadyEvent
from streaming_consumer.pipeline.fetch import BronzeMeta
from streaming_consumer.pipeline.mapping import (
    CHANGE_HOT_PROJECTION,
    SALE_HOT_PROJECTION,
    LoadedMapping,
)

# The natural key of the hot table (live uq_sscp_natural_key: the unique
# COALESCE-sentinel expression index, M-HOTKEY/0004).
NaturalKey = tuple[str, str | None, str | None]

# Numeric-shortcut categories (live numeric_value_* column comments).
_NUMERIC_CATEGORIES = frozenset({"INVENTORY", "PRICE", "COST"})


def canonical_row_hash(payload: dict[str, Any]) -> str:
    """Deterministic hash of one row's canonical (mapping-produced) payload.

    The D42 ``row_hash``: orjson with sorted keys; non-native types (Decimal,
    datetime, UUID, date) serialize via ``default=str``. Used for the
    DUPLICATE_NOOP-versus-OVERWRITTEN comparison and the duplicate audit detail.
    """
    data = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS, default=str)
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class EventRow:
    """One write-ready event-table row plus its hot contribution."""

    params: dict[str, Any]  # column -> bind value (JSONB values pre-serialized str)
    source_event_id: str
    event_ts: datetime
    natural_key: NaturalKey
    hot_contributions: dict[str, Any]  # hot column -> value (D63 projection)
    payload: dict[str, Any]  # the mapping-produced columns (hash/compare universe)
    row_hash: str
    chunk_row_index: int


def derive_source_event_id(
    payload: dict[str, Any],
    *,
    target_model: type[BaseModel],
    bronze_ref: UUID,
    chunk_row_index: int,
) -> str:
    """The D38 population rule (per the migration-0003 column comments)."""
    if target_model is StoreSkuSaleEvent:
        transaction_id = payload.get("transaction_id")
        line_item_seq = payload.get("line_item_seq")
        if transaction_id is not None and line_item_seq is not None:
            return f"{transaction_id}:{line_item_seq}"
    return f"{bronze_ref}:{chunk_row_index}"


def jsonb_param(value: Any) -> str | None:
    """Serialize a value for a JSONB bind parameter (``CAST(:p AS JSONB)`` SQL-side)."""
    if value is None:
        return None
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS, default=str).decode()


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def build_event_rows(
    event: IngressReadyEvent,
    bronze: BronzeMeta,
    loaded: LoadedMapping,
    result: MappingResult,
    *,
    tax_treatment: str | None,
) -> list[EventRow]:
    """Build the dual-write input rows from a fully validated contribution."""
    is_sale = loaded.target_model is StoreSkuSaleEvent
    event_ts_column = "source_sale_timestamp" if is_sale else "source_event_timestamp"
    rows = result.contribution.to_dicts()
    event_rows: list[EventRow] = []

    for row, chunk_row_index in zip(rows, result.source_row_indices, strict=True):
        payload: dict[str, Any] = dict(row)
        event_ts = payload[event_ts_column]
        if not isinstance(event_ts, datetime):  # unreachable post-validation; defensive
            raise TypeError(f"{event_ts_column} is not a datetime after cast: {type(event_ts).__name__}")
        source_event_id = derive_source_event_id(
            payload,
            target_model=loaded.target_model,
            bronze_ref=event.bronze_ref,
            chunk_row_index=chunk_row_index,
        )
        params: dict[str, Any] = dict(payload)
        params.update(
            id=new_uuid7(),
            tenant_id=event.tenant_id,
            store_id=event.store_id,
            source_id=event.source_id,
            source_event_id=source_event_id,
            mapping_version_id=loaded.mapping_version_id,
            trace_id=event.trace_id,
            dis_channel=bronze.dis_channel,
            ingest_metadata=jsonb_param(
                {
                    "dis_received_timestamp": event.received_ts.isoformat(),
                    "csv_row_num": chunk_row_index,
                }
            ),
        )
        if is_sale:
            params["tax_treatment"] = tax_treatment
        else:
            _populate_change_shortcuts(params, payload)
        event_rows.append(
            EventRow(
                params=params,
                source_event_id=source_event_id,
                event_ts=event_ts,
                natural_key=(
                    str(payload["sku_id"]),
                    payload.get("sku_variant"),
                    payload.get("sku_lot_batch"),
                ),
                hot_contributions=_hot_contributions(payload, is_sale=is_sale),
                payload=payload,
                row_hash=canonical_row_hash(payload),
                chunk_row_index=chunk_row_index,
            )
        )
    return event_rows


def _populate_change_shortcuts(params: dict[str, Any], payload: dict[str, Any]) -> None:
    """Typed numeric shortcuts for INVENTORY/PRICE/COST (live column comments)."""
    category = payload.get("event_category")
    numeric = category in _NUMERIC_CATEGORIES
    before = _as_decimal(payload.get("value_before")) if numeric else None
    after = _as_decimal(payload.get("value_after")) if numeric else None
    params["numeric_value_before"] = before
    params["numeric_value_after"] = after
    params["numeric_change"] = (
        after - before if category == "INVENTORY" and after is not None and before is not None else None
    )
    # JSONB payload columns: serialize whatever the mapping produced.
    for jsonb_column in ("value_before", "value_after", "change_context"):
        if jsonb_column in params:
            params[jsonb_column] = jsonb_param(params[jsonb_column])


def _hot_contributions(payload: dict[str, Any], *, is_sale: bool) -> dict[str, Any]:
    """The D63 projection: which hot columns this row asserts, with values."""
    if is_sale:
        return {hot: payload[evt] for evt, hot in SALE_HOT_PROJECTION.items() if payload.get(evt) is not None}
    category = payload.get("event_category")
    attribute = payload.get("attribute_name")
    if not isinstance(category, str) or not isinstance(attribute, str):
        return {}
    hot_column = CHANGE_HOT_PROJECTION.get((category, attribute))
    if hot_column is None:
        return {}
    value: Any = payload.get("value_after")
    if category in _NUMERIC_CATEGORIES:
        value = _as_decimal(value)
    if value is None:
        return {}
    return {hot_column: value}
