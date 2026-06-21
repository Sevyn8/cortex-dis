"""Drift guard tests — slice-05 criterion 6.

The proof ERRORS, it never skips: every input is imported directly (an absent
model or registry entry raises at import/lookup, not ``pytest.skip``), there is no
fallback default, and ``store_sku_signal_history`` raises BY DESIGN. The guard is
exercised in both directions via mutated registry copies — adding an unclassified
model column and carrying a stale registry column must both go red.

The chain back to the live schema: ``model_fields`` is reconciled against the
live ``ithina_dis_db`` columns both directions by the Slice 3 integration test
(libs/dis-canonical/tests/integration/test_schema_reconciliation.py), so
live schema <-> model <-> provenance <-> suite is closed without this pure lib
touching the DB.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import BaseModel

from dis_canonical import (
    StoreSkuChangeEvent,
    StoreSkuCurrentPosition,
    StoreSkuSaleEvent,
    StoreSkuSignalHistory,
)
from dis_core.errors import DisError, SuiteDriftError
from dis_validation import PROVENANCE, assert_no_drift, mapping_produced_columns
from dis_validation.provenance import ColumnProvenance

# Pre-widened key for monkeypatch.setitem: pytest's invariant dict[K, V] cannot
# infer K from a concrete model class against the type[BaseModel]-keyed registry.
_SALE_EVENT_KEY: type[BaseModel] = StoreSkuSaleEvent

MAPPING_FED_MODELS: list[type[BaseModel]] = [
    StoreSkuCurrentPosition,
    StoreSkuSaleEvent,
    StoreSkuChangeEvent,
]


@pytest.mark.parametrize("model", MAPPING_FED_MODELS)
def test_provenance_partitions_every_mapping_fed_model_exactly(model: type[BaseModel]) -> None:
    # Errors-not-skips: assert_no_drift raises on ANY mismatch; a clean pass means
    # the FIVE sets partition model_fields exactly, both directions
    # (enrichment_produced added in slice-5b, D95).
    assert_no_drift(model)
    provenance = PROVENANCE[model]
    union = (
        provenance.consumer_injected
        | provenance.db_generated
        | provenance.compute_owned
        | provenance.mapping_produced
        | provenance.enrichment_produced
    )
    assert union == frozenset(model.model_fields)
    total = (
        len(provenance.consumer_injected)
        + len(provenance.db_generated)
        + len(provenance.compute_owned)
        + len(provenance.mapping_produced)
        + len(provenance.enrichment_produced)
    )
    assert total == len(model.model_fields)  # pairwise disjoint


def test_consumer_injected_set_carries_the_full_injected_line() -> None:
    # The framing the whole slice rests on (OQ9): identity, trace, version stamp,
    # channel, and lineage metadata are consumer-injected on every mapping-fed model.
    for model in MAPPING_FED_MODELS:
        injected = PROVENANCE[model].consumer_injected
        assert {
            "tenant_id",
            "store_id",
            "trace_id",
            "mapping_version_id",
            "dis_channel",
            "ingest_metadata",
        } <= injected


def test_mapping_produced_counts_match_the_introspected_line() -> None:
    # Drawn from the live schema (slice-05 OQ9, corrected by the adversarial pass's
    # full-comment re-introspection): 28 / 20 / 15. The corrections: tax_treatment
    # is "Denormalized from store" (hot + sale -> consumer-injected) and the
    # numeric_value_* shortcuts are "Populated by the streaming consumer"
    # (change -> consumer-injected).
    assert len(mapping_produced_columns(StoreSkuCurrentPosition)) == 28
    assert len(mapping_produced_columns(StoreSkuSaleEvent)) == 20
    assert len(mapping_produced_columns(StoreSkuChangeEvent)) == 15


def test_store_denormalized_and_consumer_shortcut_columns_are_not_mapping_produced() -> None:
    # The adversarial-pass reclassifications, pinned by name so they cannot silently
    # revert (evidence: live column comments, cited in provenance.py). slice-5b (D98):
    # tax_treatment on the HOT model migrated consumer_injected -> enrichment_produced
    # (the lib writes it from the store, and it is canonical-shape-validated); the SALE
    # model KEEPS it consumer_injected (event path out of scope) — the D98 asymmetry.
    assert "tax_treatment" in PROVENANCE[StoreSkuCurrentPosition].enrichment_produced
    assert "tax_treatment" not in PROVENANCE[StoreSkuCurrentPosition].consumer_injected
    assert "tax_treatment" not in mapping_produced_columns(StoreSkuCurrentPosition)
    assert "tax_treatment" in PROVENANCE[StoreSkuSaleEvent].consumer_injected
    for column in ("numeric_value_before", "numeric_value_after", "numeric_change"):
        assert column in PROVENANCE[StoreSkuChangeEvent].consumer_injected


def test_event_date_is_mapping_produced_on_both_event_models() -> None:
    # Operator-confirmed judgment call (owning slice 10), marked in provenance.py.
    assert "event_date" in mapping_produced_columns(StoreSkuSaleEvent)
    assert "event_date" in mapping_produced_columns(StoreSkuChangeEvent)


def test_yesterday_retail_price_is_compute_owned() -> None:
    # Operator-confirmed judgment call (owning slice 18), marked in provenance.py.
    assert "yesterday_retail_price" in PROVENANCE[StoreSkuCurrentPosition].compute_owned
    assert "yesterday_retail_price" not in mapping_produced_columns(StoreSkuCurrentPosition)


def test_signal_history_raises_by_design() -> None:
    # D22/D31/D32: daily-compute output, no mapping_version_id, no mapping-time suite.
    with pytest.raises(SuiteDriftError, match="daily-compute output"):
        mapping_produced_columns(StoreSkuSignalHistory)
    with pytest.raises(SuiteDriftError):
        assert_no_drift(StoreSkuSignalHistory)


def test_unregistered_model_errors_rather_than_defaulting() -> None:
    from pydantic import BaseModel

    class NotCanonical(BaseModel):
        x: int

    with pytest.raises(SuiteDriftError, match="no provenance registration"):
        mapping_produced_columns(NotCanonical)


def test_guard_goes_red_when_a_model_column_is_unclassified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Direction 1: a (simulated) new canonical column not classified anywhere ->
    # SuiteDriftError naming it. It must NOT silently join any set.
    current = PROVENANCE[StoreSkuSaleEvent]
    mutated = replace(current, mapping_produced=current.mapping_produced - {"quantity"})
    monkeypatch.setitem(PROVENANCE, _SALE_EVENT_KEY, mutated)
    with pytest.raises(SuiteDriftError, match="not classified"):
        assert_no_drift(StoreSkuSaleEvent)


def test_guard_goes_red_when_the_registry_carries_a_stale_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Direction 2: a registry column the model no longer carries -> SuiteDriftError.
    current = PROVENANCE[StoreSkuSaleEvent]
    mutated = replace(current, mapping_produced=current.mapping_produced | {"dropped_column"})
    monkeypatch.setitem(PROVENANCE, _SALE_EVENT_KEY, mutated)
    with pytest.raises(SuiteDriftError, match="stale"):
        assert_no_drift(StoreSkuSaleEvent)


def test_guard_goes_red_on_double_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    current = PROVENANCE[StoreSkuSaleEvent]
    mutated = ColumnProvenance(
        consumer_injected=current.consumer_injected | {"quantity"},
        db_generated=current.db_generated,
        compute_owned=current.compute_owned,
        mapping_produced=current.mapping_produced,
    )
    monkeypatch.setitem(PROVENANCE, _SALE_EVENT_KEY, mutated)
    with pytest.raises(SuiteDriftError, match="classified in both"):
        assert_no_drift(StoreSkuSaleEvent)


def test_drift_errors_are_dis_errors() -> None:
    with pytest.raises(DisError):
        mapping_produced_columns(StoreSkuSignalHistory)
