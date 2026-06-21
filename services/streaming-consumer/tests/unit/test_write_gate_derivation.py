"""Slice 16h: the write-time completeness required set is MODEL-DERIVED.

``HOT_REQUIRED_FROM_PROJECTION`` is no longer a hand-curated literal — it is
``mandatory_mapping_produced(StoreSkuCurrentPosition, enrichment_guaranteed=…)``
(required-in-model ∩ mapping_produced, minus the enrichment value-guaranteed fields),
the same derivation the create-time gate uses. Two guarantees:

- **No behaviour change (T1):** the derived set yields IDENTICAL COMPLETE/INCOMPLETE
  verdicts to the old hand-curated literal across the mapping matrix — the difference
  ({sku_id}) is inert because guaranteed_hot_columns always covers sku_id (in every
  mapping's targets). Slice 16i subtracted currency from the required set (its value is
  enrichment-guaranteed); the enrichment union still covers it on the guaranteed side, so
  verdicts are unchanged either way. Slice 16j made product_category + unit_cost nullable,
  which DELIBERATELY changes the verdict for a snapshot omitting them (now COMPLETE) — so
  that case was retired from this T1 matrix (it is no longer behaviour-preserving against
  the pre-16h literal); its new behaviour is asserted by the reduced-set test and the
  catalogue-write e2e instead. The remaining matrix still reproduces the literal exactly.
- **Hot-model pin (T4):** the set is keyed to the hot/current-position model, NEVER
  the routed target_model — the one real trap, invisible to verdict tests (for events
  the required and guaranteed sets are disjoint, both give False either way), so it is
  asserted structurally here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import streaming_consumer.pipeline.mapping as mapping_module
from dis_canonical import StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_enrichment import CURRENT_POSITION, enrichment_fields
from dis_mapping import SourceMapping
from dis_validation import mandatory_mapping_produced

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mappings"

# A frozen copy of the PRE-16h hand-curated literal — the behaviour baseline.
_OLD_LITERAL = frozenset({"product_name", "product_category", "current_retail_price", "unit_cost"})


def _src(rename: dict[str, str]) -> SourceMapping:
    return SourceMapping.model_validate(
        {"version": 1, "rename": rename, "normalize": {}, "cast": {}, "derive": {}}
    )


def _fixture(name: str) -> SourceMapping:
    return SourceMapping.model_validate(json.loads((_FIXTURES / name).read_text()))


# The verdict matrix: (label, mapping, routed target_model).
def _cases() -> list[tuple[str, SourceMapping, type]]:
    return [
        # snapshot: complete (all 6) / currency-omitted (enrichment covers it) /
        # missing a genuinely-required projected column (product_name). The
        # "missing unit_cost" case was retired in Slice 16j: unit_cost became
        # nullable, so omitting it now classifies COMPLETE — a deliberate behaviour
        # change, not verdict drift, so it can no longer be compared against the
        # frozen pre-16h literal here (its new behaviour is proven by the
        # reduced-set test below and the catalogue-write e2e).
        (
            "snapshot_complete",
            _src(
                {
                    "a": "sku_id",
                    "b": "product_name",
                    "c": "product_category",
                    "d": "current_retail_price",
                    "e": "unit_cost",
                    "f": "currency",
                }
            ),
            StoreSkuCurrentPosition,
        ),
        (
            "snapshot_no_currency",
            _src(
                {
                    "a": "sku_id",
                    "b": "product_name",
                    "c": "product_category",
                    "d": "current_retail_price",
                    "e": "unit_cost",
                }
            ),
            StoreSkuCurrentPosition,
        ),
        (
            "snapshot_missing_product_name",
            _src({"a": "sku_id", "c": "product_category", "d": "current_retail_price", "e": "unit_cost"}),
            StoreSkuCurrentPosition,
        ),
        ("snapshot_sku_only", _src({"a": "sku_id"}), StoreSkuCurrentPosition),
        # sale: full + no-promo — events can never carry product_name/product_category,
        # so always INCOMPLETE under either required set.
        (
            "sale_full",
            _src(
                {
                    "r": "unit_retail_price",
                    "u": "unit_cost",
                    "p": "promo_identifier",
                    "c": "currency",
                    "sku": "sku_id",
                    "ts": "source_sale_timestamp",
                }
            ),
            StoreSkuSaleEvent,
        ),
        (
            "sale_no_promo",
            _src(
                {
                    "r": "unit_retail_price",
                    "u": "unit_cost",
                    "c": "currency",
                    "sku": "sku_id",
                    "ts": "source_sale_timestamp",
                }
            ),
            StoreSkuSaleEvent,
        ),
        # production fixtures (the live mappings) — both INCOMPLETE today.
        ("prod_sale_pos_v1", _fixture("sale_pos_v1.json"), StoreSkuSaleEvent),
    ]


def test_derived_set_reproduces_old_literal_verdicts(monkeypatch: pytest.MonkeyPatch) -> None:
    # T1: the derived set classifies every case exactly as the old 4-member literal —
    # the no-behaviour-change proof for the refactor.
    for label, source, target in _cases():
        derived = mapping_module.classify_hot_completeness(source, target)
        with monkeypatch.context() as mp:
            mp.setattr(mapping_module, "HOT_REQUIRED_FROM_PROJECTION", _OLD_LITERAL)
            old = mapping_module.classify_hot_completeness(source, target)
        assert derived == old, f"verdict drift for {label} ({target.__name__}): derived={derived} old={old}"


def test_required_set_is_the_model_derivation_not_a_literal() -> None:
    # T4 (mutation-evident): the constant IS the hot-model derivation with the enrichment
    # value-guaranteed fields subtracted (Slice 16i). A future model nullability change
    # (16j) is reflected with no edit here; a re-baked literal breaks this.
    assert mapping_module.HOT_REQUIRED_FROM_PROJECTION == mandatory_mapping_produced(
        StoreSkuCurrentPosition, frozenset(enrichment_fields(CURRENT_POSITION))
    )
    # The concrete 3-member set today: currency subtracted as enrichment-guaranteed
    # (16i), and product_category + unit_cost dropped once they became nullable (16j).
    assert mapping_module.HOT_REQUIRED_FROM_PROJECTION == frozenset(
        {"sku_id", "product_name", "current_retail_price"}
    )


def test_required_set_is_hot_model_pinned_not_routed_target() -> None:
    # T4 (the trap): pinned to the HOT model, never the routed target. Substituting
    # the sale-event derivation would compute a DIFFERENT set — asserted structurally
    # because verdicts alone cannot catch it (event required/guaranteed sets are disjoint).
    assert mapping_module.HOT_REQUIRED_FROM_PROJECTION != mandatory_mapping_produced(StoreSkuSaleEvent)
