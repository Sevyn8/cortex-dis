"""The template-type vocabulary (Slice 14d): the one shared definition.

The keys + the type→model mapping live here so the BFF (catalog, validator, type
endpoint) and the streaming consumer (routing) read one source. These tests pin
the mapping to the canonical models, not to a hardcoded list elsewhere.
"""

from __future__ import annotations

import pytest

from dis_canonical import StoreSkuChangeEvent, StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_validation import (
    INVENTORY_CHANGE,
    MODEL_BY_TYPE,
    SALES,
    SNAPSHOT,
    TEMPLATE_TYPES,
    is_template_type,
    model_for_template_type,
)


def test_vocabulary_is_the_three_keys_in_listing_order() -> None:
    assert TEMPLATE_TYPES == (SNAPSHOT, SALES, INVENTORY_CHANGE)


def test_model_by_type_targets_the_canonical_models() -> None:
    assert MODEL_BY_TYPE == {
        SNAPSHOT: StoreSkuCurrentPosition,
        SALES: StoreSkuSaleEvent,
        INVENTORY_CHANGE: StoreSkuChangeEvent,
    }
    # The vocabulary keys and the model-mapping keys are the same set — no key
    # without a target, no target without a key.
    assert set(MODEL_BY_TYPE) == set(TEMPLATE_TYPES)


def test_snapshot_targets_the_hot_table() -> None:
    assert model_for_template_type(SNAPSHOT) is StoreSkuCurrentPosition


def test_is_template_type_membership() -> None:
    assert all(is_template_type(t) for t in TEMPLATE_TYPES)
    assert not is_template_type("bogus")
    assert not is_template_type("")


def test_model_for_unknown_type_raises() -> None:
    with pytest.raises(KeyError):
        model_for_template_type("bogus")
