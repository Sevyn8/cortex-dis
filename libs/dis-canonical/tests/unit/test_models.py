"""Unit tests for the canonical models.

Covers: construction, round-trip serialize/validate, field types (UUID / Decimal /
tz-aware datetime / enum), post-D36 composite store keying, mapping_version_id
presence (3 mapping tables) vs absence (signal_history), and DDL-derived
constraints (varchar length, char(3) currency, enum vocab).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from dis_canonical import (
    StoreSkuChangeEvent,
    StoreSkuCurrentPosition,
    StoreSkuSaleEvent,
    StoreSkuSignalHistory,
)
from dis_canonical.shared import TaxTreatment
from dis_core.ids import new_uuid7
from dis_core.timestamps import now_utc

# -- factories -----------------------------------------------------------------


def _sale_event(**overrides: object) -> StoreSkuSaleEvent:
    data: dict[str, object] = dict(
        event_date=date(2026, 6, 3),
        tenant_id=new_uuid7(),
        store_id=new_uuid7(),
        sku_id="SKU-1",
        event_subtype="SALE",
        source_sale_timestamp=now_utc(),
        quantity=Decimal("2.000"),
        unit_retail_price=Decimal("9.9900"),
        unit_sale_price=Decimal("8.5000"),
        tax_treatment="INCLUSIVE",
        currency="INR",
        source_id="manual_csv_upload",  # NOT NULL (D38, migration 0003)
        source_event_id="TXN-1:1",  # NOT NULL (D38; transaction_id:line_item_seq form)
        mapping_version_id=1,
        trace_id=new_uuid7(),
        dis_channel="csv_upload",
    )
    data.update(overrides)
    return StoreSkuSaleEvent(**data)


def _current_position(**overrides: object) -> StoreSkuCurrentPosition:
    data: dict[str, object] = dict(
        tenant_id=new_uuid7(),
        store_id=new_uuid7(),
        sku_id="SKU-1",
        product_name="Widget",
        product_category="Hardware",
        current_retail_price=Decimal("12.5000"),
        unit_cost=Decimal("7.0000"),
        tax_treatment="EXCLUSIVE",
        currency="INR",
        mapping_version_id=3,
        trace_id=new_uuid7(),
        dis_channel="api",
    )
    data.update(overrides)
    return StoreSkuCurrentPosition(**data)


# -- round-trip + types --------------------------------------------------------


def test_sale_event_roundtrips_with_correct_types() -> None:
    evt = _sale_event()
    dumped = evt.model_dump()
    restored = StoreSkuSaleEvent.model_validate(dumped)
    assert restored == evt

    assert isinstance(evt.tenant_id, UUID)
    assert isinstance(evt.store_id, UUID)
    assert isinstance(evt.unit_sale_price, Decimal)
    assert evt.source_sale_timestamp.tzinfo is not None
    assert evt.tax_treatment is TaxTreatment.INCLUSIVE  # string coerced to enum
    assert evt.id is None  # DB-generated (uuidv7 default), optional in-memory


def test_current_position_roundtrips() -> None:
    pos = _current_position()
    restored = StoreSkuCurrentPosition.model_validate(pos.model_dump())
    assert restored == pos
    assert pos.regulatory_flag is None  # DB default false; optional in-memory
    assert pos.sku_variant is None  # nullable column


def test_change_event_constructs() -> None:
    evt = StoreSkuChangeEvent(
        event_date=date(2026, 6, 3),
        tenant_id=new_uuid7(),
        store_id=new_uuid7(),
        sku_id="SKU-9",
        event_category="PRICE",
        event_subtype="retail_price_update",
        source_event_timestamp=now_utc(),
        value_after={"price": "10.00"},
        source_id="erp_nightly",  # NOT NULL (D38, migration 0003)
        source_event_id="0197a000-0000-7000-8000-000000000000:42",  # D65 fallback form
        mapping_version_id=2,
        trace_id=new_uuid7(),
        dis_channel="csv_erp",
    )
    assert StoreSkuChangeEvent.model_validate(evt.model_dump()) == evt


def test_signal_history_constructs() -> None:
    sig = StoreSkuSignalHistory(
        as_of_date=date(2026, 6, 3),
        tenant_id=new_uuid7(),
        store_id=new_uuid7(),
        sku_id="SKU-1",
        velocity_7day=Decimal("3.2500"),
        trace_id=new_uuid7(),
    )
    assert StoreSkuSignalHistory.model_validate(sig.model_dump()) == sig


# -- structural assertions -----------------------------------------------------


def test_composite_store_key_on_every_model() -> None:
    for model in (
        StoreSkuCurrentPosition,
        StoreSkuSaleEvent,
        StoreSkuChangeEvent,
        StoreSkuSignalHistory,
    ):
        assert "tenant_id" in model.model_fields
        assert "store_id" in model.model_fields


def test_mapping_version_present_on_mapping_tables() -> None:
    for model in (StoreSkuCurrentPosition, StoreSkuSaleEvent, StoreSkuChangeEvent):
        field = model.model_fields["mapping_version_id"]
        assert field.is_required()  # NOT NULL, no default (D22)


def test_signal_history_has_no_mapping_version_id() -> None:
    # Daily-compute output, not mapping-produced (D22/D31/D32).
    assert "mapping_version_id" not in StoreSkuSignalHistory.model_fields


def test_dedup_key_columns_required_on_event_models() -> None:
    # D38 resolution (migration 0003): the D33 dedup-key columns are NOT NULL
    # on both event models; the hot table carries no dedup-key columns and its
    # D64 event-time reference is nullable (NULL = never event-written).
    for model in (StoreSkuSaleEvent, StoreSkuChangeEvent):
        assert model.model_fields["source_id"].is_required()
        assert model.model_fields["source_event_id"].is_required()
    assert "source_id" not in StoreSkuCurrentPosition.model_fields
    assert "source_event_id" not in StoreSkuCurrentPosition.model_fields
    assert not StoreSkuCurrentPosition.model_fields["last_source_event_at"].is_required()


# -- DDL-derived constraints ---------------------------------------------------


def test_missing_required_field_raises() -> None:
    with pytest.raises(ValidationError):
        _sale_event(mapping_version_id=None)


def test_bad_enum_value_raises() -> None:
    with pytest.raises(ValidationError):
        _sale_event(tax_treatment="NOPE")


def test_bad_event_subtype_literal_raises() -> None:
    with pytest.raises(ValidationError):
        _sale_event(event_subtype="GIFT")


def test_varchar_length_enforced() -> None:
    with pytest.raises(ValidationError):
        _sale_event(sku_id="x" * 129)  # varchar(128)


def test_currency_must_be_three_chars() -> None:
    with pytest.raises(ValidationError):
        _sale_event(currency="IN")
    with pytest.raises(ValidationError):
        _sale_event(currency="INRX")


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        _sale_event(not_a_real_column="x")
