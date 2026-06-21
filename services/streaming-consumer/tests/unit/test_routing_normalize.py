"""Routing (sale-versus-change), the D38 source_event_id rule, and the D63
projections — the write-shape units.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from dis_canonical import StoreSkuChangeEvent, StoreSkuSaleEvent
from dis_core.errors import MappingConfigError
from dis_mapping import SourceMapping
from streaming_consumer.pipeline.mapping import route_target_model
from streaming_consumer.pipeline.normalize import (
    EventRow,
    canonical_row_hash,
    derive_source_event_id,
)
from streaming_consumer.sinks.canonical import _group_hot  # noqa: PLC2701 - white-box unit

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mappings"
_BRONZE_REF = UUID("019e9508-0000-7000-8000-00000000000b")


def _mapping(name: str) -> SourceMapping:
    return SourceMapping.model_validate(json.loads((_FIXTURES / name).read_text()))


# -- routing --------------------------------------------------------------------


def test_sale_mapping_routes_to_sale_model() -> None:
    target = route_target_model(_mapping("sale_pos_v1.json"), tenant_id="t", trace_id="r")
    assert target is StoreSkuSaleEvent


def test_change_mapping_routes_to_change_model() -> None:
    target = route_target_model(_mapping("inventory_count_v1.json"), tenant_id="t", trace_id="r")
    assert target is StoreSkuChangeEvent


def test_ambiguous_targets_raise() -> None:
    # Only shared columns (sku_id + event_date + event_subtype fit BOTH event
    # models) -> zero-or-two matches is a loud config error, never a guess.
    ambiguous = SourceMapping.model_validate(
        {
            "version": 1,
            "rename": {"sku": "sku_id", "subtype": "event_subtype"},
            "normalize": {},
            "cast": {},
            "derive": {},
        }
    )
    with pytest.raises(MappingConfigError, match="exactly one"):
        route_target_model(ambiguous, tenant_id="t", trace_id="r")


def test_hot_only_targets_raise() -> None:
    # A catalogue-ish mapping fits NO event model: v1.0 chunks are event chunks.
    hot_only = SourceMapping.model_validate(
        {
            "version": 1,
            "rename": {"name": "product_name", "cat": "product_category"},
            "normalize": {},
            "cast": {},
            "derive": {},
        }
    )
    with pytest.raises(MappingConfigError, match="exactly one"):
        route_target_model(hot_only, tenant_id="t", trace_id="r")


# -- the D38 population rule ------------------------------------------------------


def test_sale_with_native_ids_uses_transaction_line() -> None:
    key = derive_source_event_id(
        {"transaction_id": "T-9", "line_item_seq": 3},
        target_model=StoreSkuSaleEvent,
        bronze_ref=_BRONZE_REF,
        chunk_row_index=7,
    )
    assert key == "T-9:3"


def test_sale_without_native_ids_falls_back() -> None:
    payloads: list[dict[str, object]] = [
        {"transaction_id": None, "line_item_seq": 1},
        {"transaction_id": "T", "line_item_seq": None},
        {},
    ]
    for payload in payloads:
        key = derive_source_event_id(
            payload, target_model=StoreSkuSaleEvent, bronze_ref=_BRONZE_REF, chunk_row_index=7
        )
        assert key == f"{_BRONZE_REF}:7"  # D65: redelivery-stable fallback


def test_change_always_falls_back() -> None:
    key = derive_source_event_id(
        {"transaction_id": "T-9", "line_item_seq": 3},  # even if present, change has no native id
        target_model=StoreSkuChangeEvent,
        bronze_ref=_BRONZE_REF,
        chunk_row_index=0,
    )
    assert key == f"{_BRONZE_REF}:0"


# -- REVISED D63: load-time completeness classification ---------------------------


def test_change_projection_pairs_are_identity() -> None:
    # The convention guard until the registry anchor lands (carried limit 6):
    # every change pair maps attribute_name X to hot column X — an IDENTITY.
    # A typo'd pair would otherwise mis-route an UPDATE into the wrong hot
    # column (the conservative classifier protects only the CREATE path); this
    # assertion makes that a loud test failure, not a silent bad write.
    from streaming_consumer.pipeline.mapping import CHANGE_HOT_PROJECTION

    for (category, attribute), hot_column in CHANGE_HOT_PROJECTION.items():
        assert hot_column == attribute, (
            f"({category}, {attribute}) -> {hot_column}: change pairs must be identity "
            "mappings until an independent register anchor exists"
        )


def test_production_fixture_mappings_classify_incomplete() -> None:
    # No current production mapping can create hot rows: sale targets never
    # carry product_name/product_category; change mappings guarantee at most
    # one hot column.
    from streaming_consumer.pipeline.mapping import classify_hot_completeness

    sale = _mapping("sale_pos_v1.json")
    change = _mapping("inventory_count_v1.json")
    assert classify_hot_completeness(sale, StoreSkuSaleEvent) is False
    assert classify_hot_completeness(change, StoreSkuChangeEvent) is False


def test_guaranteed_hot_columns_derivation() -> None:
    from streaming_consumer.pipeline.mapping import guaranteed_hot_columns

    sale = _mapping("sale_pos_v1.json")
    # sale_pos_v1 renames unit_retail_price + derives currency; no unit_cost or
    # promo_identifier targets — the registry image is exactly these two.
    assert guaranteed_hot_columns(sale, StoreSkuSaleEvent) == frozenset({"current_retail_price", "currency"})
    # inventory_count_v1 pins event_category=INVENTORY + attribute_name=stock_qty
    # as derive CONSTANTS -> the single statically guaranteed hot column.
    change = _mapping("inventory_count_v1.json")
    assert guaranteed_hot_columns(change, StoreSkuChangeEvent) == frozenset({"stock_qty"})


def test_completeness_logic_superset_and_check_implications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The classification's two clauses, exercised directly (no production
    # mapping reaches them — white-box on the load-time discriminator):
    # required-superset AND presence-pairing CHECK implications
    # (promo_identifier => promo_price; expiry triple all-or-none).
    import streaming_consumer.pipeline.mapping as mapping_module

    sale_full = SourceMapping.model_validate(
        {
            "version": 1,
            "rename": {
                "r": "unit_retail_price",
                "u": "unit_cost",
                "p": "promo_identifier",
                "c": "currency",
                "sku": "sku_id",
                "ts": "source_sale_timestamp",
            },
            "normalize": {},
            "cast": {},
            "derive": {},
        }
    )
    guaranteed = mapping_module.guaranteed_hot_columns(sale_full, StoreSkuSaleEvent)
    assert guaranteed == frozenset({"current_retail_price", "unit_cost", "promo_identifier", "currency"})
    # Shrink the required set so the superset clause passes; the promo pairing
    # implication must then FAIL the classification (promo_identifier without
    # promo_price).
    monkeypatch.setattr(
        mapping_module,
        "HOT_REQUIRED_FROM_PROJECTION",
        frozenset({"current_retail_price", "currency"}),
    )
    assert mapping_module.classify_hot_completeness(sale_full, StoreSkuSaleEvent) is False
    # Without the promo target, both clauses hold -> complete.
    sale_no_promo = SourceMapping.model_validate(
        {
            "version": 1,
            "rename": {
                "r": "unit_retail_price",
                "u": "unit_cost",
                "c": "currency",
                "sku": "sku_id",
                "ts": "source_sale_timestamp",
            },
            "normalize": {},
            "cast": {},
            "derive": {},
        }
    )
    assert mapping_module.classify_hot_completeness(sale_no_promo, StoreSkuSaleEvent) is True


def test_row_hash_is_deterministic_and_value_sensitive() -> None:
    a = canonical_row_hash({"q": Decimal("1.000"), "s": "x"})
    assert a == canonical_row_hash({"s": "x", "q": Decimal("1.000")})  # key order free
    assert a != canonical_row_hash({"q": Decimal("2.000"), "s": "x"})


# -- the D63 hot grouping (column-scoped, event-time-wins within the batch) -------


def _row(ts: datetime, contributions: dict[str, object], key: str = "SKU") -> EventRow:
    return EventRow(
        params={},
        source_event_id=f"k-{ts.minute}",
        event_ts=ts,
        natural_key=(key, None, None),
        hot_contributions=dict(contributions),
        payload={},
        row_hash="h",
        chunk_row_index=ts.minute,
    )


def test_group_hot_latest_event_time_wins_per_column() -> None:
    t0 = datetime(2026, 6, 5, 10, 0, tzinfo=UTC)
    t1 = datetime(2026, 6, 5, 11, 0, tzinfo=UTC)
    groups = _group_hot(
        [
            _row(t1, {"current_retail_price": Decimal("12.00")}),  # later, first in chunk
            _row(t0, {"current_retail_price": Decimal("9.00"), "unit_cost": Decimal("4.00")}),
        ]
    )
    assert len(groups) == 1
    group = groups[0]
    # Column-scoped: the older row may NOT overwrite the newer price, but its
    # unit_cost (which no newer row asserted) still contributes.
    assert group.projected["current_retail_price"] == Decimal("12.00")
    assert group.projected["unit_cost"] == Decimal("4.00")
    assert group.last_source_event_at == t1


def test_group_hot_distinct_keys_stay_distinct() -> None:
    t0 = datetime(2026, 6, 5, 10, 0, tzinfo=UTC)
    groups = _group_hot(
        [
            _row(t0, {"stock_qty": Decimal("1")}, key="A"),
            _row(t0, {"stock_qty": Decimal("2")}, key="B"),
        ]
    )
    assert len(groups) == 2
