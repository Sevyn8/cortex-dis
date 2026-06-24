"""Unit tests for the Atlas A2 sink resolver (no DB).

Proves the (vertical, template_type) -> table generalization:
- retail resolves to the EXISTING canonical.* names (byte-identical to pre-A2), the
  hinge of zero regression;
- a second vertical resolves to canonical_<vertical>.* through the SAME resolver,
  with the namespace passed as declared data (never computed by suffixing);
- the resolver fails loud on an undeclared vertical or an unknown template_type.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from dis_core.errors import SinkResolutionError
from dis_validation import INVENTORY_CHANGE, MODEL_BY_TYPE, SALES, SNAPSHOT
from streaming_consumer.sinks.canonical import (
    _TEMPLATE_TYPE_BY_MODEL,
    resolve_sink,
    vertical_for_tenant,
)


def test_retail_resolves_to_the_existing_canonical_names() -> None:
    # The four current strings the write path produced before A2 (byte-identical).
    assert resolve_sink("retail", SNAPSHOT) == "canonical.store_sku_current_position"
    assert resolve_sink("retail", SALES) == "canonical.store_sku_sale_events"
    assert resolve_sink("retail", INVENTORY_CHANGE) == "canonical.store_sku_change_events"
    # written_to_table on the catalogue path is the snapshot sink; on the event path
    # it is the resolved event table. Both are covered by the three above.
    assert resolve_sink("retail", SNAPSHOT) == "canonical.store_sku_current_position"


def test_vertical_for_tenant_is_stubbed_to_retail() -> None:
    assert vertical_for_tenant(UUID("00000000-0000-7000-8000-000000000001")) == "retail"
    assert vertical_for_tenant("any-tenant-id") == "retail"


def test_second_vertical_resolves_through_the_same_resolver() -> None:
    # Declared data passed explicitly (not computed by suffixing, no global mutation):
    # a new vertical resolves to its declared namespace, proving the generalization
    # without a pharma table existing.
    pharma = {"pharma": "canonical_pharma"}
    assert (
        resolve_sink("pharma", SNAPSHOT, namespaces=pharma) == "canonical_pharma.store_sku_current_position"
    )
    assert resolve_sink("pharma", SALES, namespaces=pharma) == "canonical_pharma.store_sku_sale_events"
    assert (
        resolve_sink("pharma", INVENTORY_CHANGE, namespaces=pharma)
        == "canonical_pharma.store_sku_change_events"
    )


def test_undeclared_vertical_fails_loud() -> None:
    with pytest.raises(SinkResolutionError) as exc:
        resolve_sink("logistics", SNAPSHOT)
    assert exc.value.vertical == "logistics"


def test_unknown_template_type_fails_loud() -> None:
    with pytest.raises(SinkResolutionError) as exc:
        resolve_sink("retail", "not_a_template_type")
    assert exc.value.template_type == "not_a_template_type"


def test_model_by_type_inversion_is_total_and_injective() -> None:
    # The inversion built at import preserves length (no silently dropped entry).
    assert len(_TEMPLATE_TYPE_BY_MODEL) == len(MODEL_BY_TYPE)
    for template_type, model in MODEL_BY_TYPE.items():
        assert _TEMPLATE_TYPE_BY_MODEL[model] == template_type
