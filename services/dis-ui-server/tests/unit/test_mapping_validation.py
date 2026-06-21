"""The four-step mapping_rules gate (slice 14b principle 4) — config never stores invalid.

Pure unit tests (no app, no DB): the gate runs entirely before any write, so a
rejection here is exactly what the POST/PATCH handlers turn into a 400.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from dis_canonical import StoreSkuChangeEvent, StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_core.errors import InvalidTemplateTypeError, MappingConfigError
from dis_ui_server.mapping_validation import (
    enrichment_guaranteed_for,
    route_target_model,
    validate_mapping_rules,
    validate_mapping_rules_for_type,
)
from dis_validation import mandatory_mapping_produced

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"


def _sale_rules() -> dict[str, Any]:
    """A complete, valid sale-event template (every mandatory column provided)."""
    return {
        "version": 1,
        "rename": {
            "item_code": "sku_id",
            "qty": "quantity",
            "price": "unit_retail_price",
            "paid": "unit_sale_price",
            "ts": "source_sale_timestamp",
            "kind": "event_subtype",
        },
        "normalize": {
            "quantity": [
                {"op": "parse_decimal", "args": {"decimal_separator": ".", "thousands_separator": None}}
            ],
            "source_sale_timestamp": [
                {"op": "parse_datetime", "args": {"format": "%Y-%m-%d %H:%M:%S", "timezone": "UTC"}}
            ],
        },
        "cast": {
            "quantity": {"type": "decimal", "precision": 14, "scale": 3},
            "source_sale_timestamp": {"type": "datetime"},
        },
        "derive": {
            "event_date": [{"op": "date_from_datetime", "args": {"source_column": "source_sale_timestamp"}}],
            "currency": [{"op": "constant", "args": {"value": "EUR"}}],
        },
    }


def _change_rules(category: str, attribute: str) -> dict[str, Any]:
    """A change-event template carrying only the polymorphic-common mandatory set."""
    return {
        "version": 1,
        "rename": {
            "item": "sku_id",
            "when": "source_event_timestamp",
            "subtype": "event_subtype",
            "new_value": "value_after",
        },
        "normalize": {
            "source_event_timestamp": [
                {"op": "parse_datetime", "args": {"format": "%Y-%m-%d %H:%M:%S", "timezone": "UTC"}}
            ],
        },
        "cast": {"source_event_timestamp": {"type": "datetime"}},
        "derive": {
            "event_date": [{"op": "date_from_datetime", "args": {"source_column": "source_event_timestamp"}}],
            "event_category": [{"op": "constant", "args": {"value": category}}],
            "attribute_name": [{"op": "constant", "args": {"value": attribute}}],
        },
    }


# -- the valid paths --------------------------------------------------------------


def test_valid_sale_template_routes_to_sale_events() -> None:
    source = validate_mapping_rules(_sale_rules(), tenant_id=TENANT_A)
    assert route_target_model(source, tenant_id=TENANT_A) is StoreSkuSaleEvent


def test_pricing_only_and_inventory_only_change_templates_both_pass() -> None:
    # The polymorphic-coverage proof (plan-mode confirmation, operator-accepted):
    # mandatory change columns are the five common keys/discriminators only, so
    # two templates differing solely in their derive constants both validate.
    for category, attribute in (("PRICE", "current_retail_price"), ("INVENTORY", "stock_qty")):
        source = validate_mapping_rules(_change_rules(category, attribute), tenant_id=TENANT_A)
        assert route_target_model(source, tenant_id=TENANT_A) is StoreSkuChangeEvent


def test_mandatory_sets_are_derived_not_hardcoded() -> None:
    assert set(mandatory_mapping_produced(StoreSkuSaleEvent)) == {
        "event_date",
        "sku_id",
        "event_subtype",
        "source_sale_timestamp",
        "quantity",
        "unit_retail_price",
        "unit_sale_price",
        "currency",
    }
    assert set(mandatory_mapping_produced(StoreSkuChangeEvent)) == {
        "event_date",
        "sku_id",
        "event_category",
        "event_subtype",
        "source_event_timestamp",
    }


# -- the rejection paths (each a clean MappingConfigError, never a write) ----------


def test_missing_locale_declaration_is_refused() -> None:
    rules = _sale_rules()
    del rules["normalize"]["quantity"][0]["args"]["thousands_separator"]
    with pytest.raises(MappingConfigError, match="thousands_separator"):
        validate_mapping_rules(rules, tenant_id=TENANT_A)


def test_unknown_shape_key_is_refused() -> None:
    rules = _sale_rules()
    rules["transforms"] = {}  # the D49-stale field name; extra="forbid" catches it
    with pytest.raises(MappingConfigError, match="do not parse"):
        validate_mapping_rules(rules, tenant_id=TENANT_A)


def test_unknown_canonical_target_is_refused() -> None:
    rules = _sale_rules()
    rules["rename"]["x"] = "not_a_canonical_column"
    with pytest.raises(MappingConfigError, match="not_a_canonical_column"):
        validate_mapping_rules(rules, tenant_id=TENANT_A)


def test_consumer_injected_target_is_refused() -> None:
    # trace_id is a real canonical column but NEVER mapping-produced.
    rules = _sale_rules()
    rules["rename"]["t"] = "trace_id"
    with pytest.raises(MappingConfigError, match="fit 0 event models"):
        validate_mapping_rules(rules, tenant_id=TENANT_A)


def test_ambiguous_shared_only_targets_are_refused() -> None:
    rules = {"version": 1, "rename": {"a": "sku_id"}, "normalize": {}, "cast": {}, "derive": {}}
    with pytest.raises(MappingConfigError, match="fit 2 event models"):
        validate_mapping_rules(rules, tenant_id=TENANT_A)


def test_missing_mandatory_column_is_refused() -> None:
    rules = _sale_rules()
    del rules["derive"]["currency"]
    with pytest.raises(MappingConfigError, match=r"mandatory sale_event column\(s\) \['currency'\]"):
        validate_mapping_rules(rules, tenant_id=TENANT_A)


def test_empty_rename_is_refused() -> None:
    rules = {"version": 1, "rename": {}, "normalize": {}, "cast": {}, "derive": {}}
    with pytest.raises(MappingConfigError, match="no rename targets"):
        validate_mapping_rules(rules, tenant_id=TENANT_A)


def test_rejection_paths_do_not_mutate_the_input() -> None:
    rules = _sale_rules()
    frozen = copy.deepcopy(rules)
    rules["rename"]["x"] = "not_a_canonical_column"
    with pytest.raises(MappingConfigError):
        validate_mapping_rules(rules, tenant_id=TENANT_A)
    del rules["rename"]["x"]
    assert rules == frozen


# -- the type-keyed gate (Slice 14d) ----------------------------------------------


def _snapshot_rules() -> dict[str, Any]:
    """A complete, valid catalogue (snapshot) template: every mandatory hot column."""
    return {
        "version": 1,
        "rename": {
            "code": "sku_id",
            "name": "product_name",
            "cat": "product_category",
            "price": "current_retail_price",
            "cost": "unit_cost",
        },
        "normalize": {},
        "cast": {},
        "derive": {"currency": [{"op": "constant", "args": {"value": "EUR"}}]},
    }


def test_valid_snapshot_template_validates_for_the_snapshot_type() -> None:
    source = validate_mapping_rules_for_type(_snapshot_rules(), template_type="snapshot", tenant_id=TENANT_A)
    # currency is mapping-produced (file-supplied via a constant derive); the
    # validated document round-trips it.
    assert "currency" in source.target_columns


def test_valid_sale_template_validates_for_the_sales_type() -> None:
    source = validate_mapping_rules_for_type(_sale_rules(), template_type="sales", tenant_id=TENANT_A)
    assert "quantity" in source.target_columns


def test_hot_targets_are_rejected_for_an_event_type() -> None:
    # product_name/product_category are hot columns, not sale-event columns:
    # illegal for the sales type (one direction of the type-keyed legality rule).
    with pytest.raises(MappingConfigError, match="not legal for template_type 'sales'"):
        validate_mapping_rules_for_type(_snapshot_rules(), template_type="sales", tenant_id=TENANT_A)


def test_event_targets_are_rejected_for_the_snapshot_type() -> None:
    # source_sale_timestamp / quantity are sale-event columns, illegal for snapshot
    # (the other direction).
    with pytest.raises(MappingConfigError, match="not legal for template_type 'snapshot'"):
        validate_mapping_rules_for_type(_sale_rules(), template_type="snapshot", tenant_id=TENANT_A)


def test_snapshot_omitting_currency_is_accepted_currency_enrichment_guaranteed() -> None:
    # Slice 16i (D95): currency's VALUE is enrichment-guaranteed on the current-position
    # path, so the create gate no longer demands the mapping supply it (pre-16i this was
    # a 400). currency stays mapping-produced by ORIGIN — still legal to MAP, just not
    # required.
    rules = _snapshot_rules()
    del rules["derive"]["currency"]
    source = validate_mapping_rules_for_type(rules, template_type="snapshot", tenant_id=TENANT_A)
    assert "currency" not in source.target_columns
    # The derived mandatory set for the hot model excludes the enrichment-guaranteed currency.
    hot_mandatory = mandatory_mapping_produced(
        StoreSkuCurrentPosition, enrichment_guaranteed_for(StoreSkuCurrentPosition)
    )
    assert "currency" not in hot_mandatory
    # Slice 16j: product_category and unit_cost became nullable, so they too dropped from
    # the derived mandatory set with no gate edit (the auto-follow this slice relies on).
    assert hot_mandatory == {
        "sku_id",
        "product_name",
        "current_retail_price",
    }


def test_snapshot_promo_identifier_requires_promo_price() -> None:
    rules = _snapshot_rules()
    rules["rename"]["promo"] = "promo_identifier"  # without promo_price
    with pytest.raises(MappingConfigError, match="promo_price"):
        validate_mapping_rules_for_type(rules, template_type="snapshot", tenant_id=TENANT_A)


def test_snapshot_partial_expiry_triple_is_refused() -> None:
    rules = _snapshot_rules()
    rules["rename"]["exp"] = "expiry_date"  # without expiry_source / expiry_confidence
    with pytest.raises(MappingConfigError, match="expiry"):
        validate_mapping_rules_for_type(rules, template_type="snapshot", tenant_id=TENANT_A)


def test_unknown_template_type_is_refused() -> None:
    with pytest.raises(InvalidTemplateTypeError, match="unknown template_type 'bogus'"):
        validate_mapping_rules_for_type(_sale_rules(), template_type="bogus", tenant_id=TENANT_A)


def test_catalogue_target_is_legal_only_by_type_not_event_routing() -> None:
    # The hot table is NOT in the event routing universe (route_target_model still
    # rejects a hot-only mapping), proving the catalogue target is legal by TYPE,
    # not by adding the hot table to event routing.
    source = validate_mapping_rules_for_type(_snapshot_rules(), template_type="snapshot", tenant_id=TENANT_A)
    assert StoreSkuCurrentPosition is StoreSkuCurrentPosition  # sanity
    with pytest.raises(MappingConfigError):
        route_target_model(source, tenant_id=TENANT_A)
