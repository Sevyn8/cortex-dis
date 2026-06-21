"""slice-5b guard-integrity (§8b): the canonical-shape drift guard was widened to
admit enrichment-produced columns (``owned ⊆ mapping_produced ∪ enrichment_produced``).
Relaxing a deliberately-strict guard fails SILENTLY when wrong, so these prove the
widening admits ENRICHMENT — not anything — and is SCOPED to the hot model.

They are rejection-assertions, so they go RED against a bare accept-anything guard and
GREEN against the correct guard (the G4 bite-proof / mutation check: temporarily replace
the ``off_universe`` check in ``materialize_canonical_shape`` with accept-anything and
confirm ``test_guard_still_rejects_an_unauthorized_column`` turns red, then revert). The
proofs ERROR, never skip.
"""

from __future__ import annotations

import pytest

from dis_canonical import StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_core.errors import SuiteDriftError
from dis_validation import (
    PROVENANCE,
    CanonicalShapeSuiteDef,
    enrichment_produced_columns,
    mapping_produced_columns,
    materialize_canonical_shape,
)

# A minimal valid snapshot owned-set: the mapping-required hot columns + currency
# (mapping-produced) + the enrichment-produced tax_treatment — what validate_post hands
# the suite on the current-position path.
_SSCP_OWNED: tuple[str, ...] = (
    "sku_id",
    "product_name",
    "product_category",
    "current_retail_price",
    "unit_cost",
    "currency",
    "tax_treatment",
)


def test_enrichment_produced_column_is_accepted_on_the_hot_model() -> None:
    # The relaxation WORKS: tax_treatment (enrichment_produced) materializes as a real
    # enum-gated column rather than being rejected as off-universe.
    schema = materialize_canonical_shape(
        CanonicalShapeSuiteDef(target_model=StoreSkuCurrentPosition, owned_columns=_SSCP_OWNED)
    )
    assert "tax_treatment" in schema.columns


@pytest.mark.parametrize("intruder", ["trace_id", "tenant_id", "__not_a_column__"])
def test_guard_still_rejects_an_unauthorized_column(intruder: str) -> None:
    # G1 (the bite-proof): a column in NEITHER mapping_produced NOR enrichment_produced
    # must STILL be rejected. trace_id/tenant_id are consumer-injected; __not_a_column__
    # is fabricated. Expected from the rule (no quality-gated producer), not the code.
    with pytest.raises(SuiteDriftError):
        materialize_canonical_shape(
            CanonicalShapeSuiteDef(
                target_model=StoreSkuCurrentPosition,
                owned_columns=(*_SSCP_OWNED, intruder),
            )
        )


def test_relaxation_is_scoped_to_the_hot_model_event_path_still_rejects() -> None:
    # G2: tax_treatment is enrichment_produced for the hot model ONLY; on the SALE model
    # it stays consumer_injected, so owning it must STILL raise — the relaxation did not
    # leak to the event path (the D98 asymmetry, enforced at the validation layer).
    sale_owned = ("sku_id", "event_subtype", "source_sale_timestamp", "tax_treatment")
    with pytest.raises(SuiteDriftError):
        materialize_canonical_shape(
            CanonicalShapeSuiteDef(target_model=StoreSkuSaleEvent, owned_columns=sale_owned)
        )


def test_enrichment_partition_is_disjoint_on_the_hot_model() -> None:
    # G3: tax_treatment lives in exactly ONE partition (enrichment_produced) for the hot
    # model — the new partition cannot make a column's classification ambiguous.
    p = PROVENANCE[StoreSkuCurrentPosition]
    assert "tax_treatment" in p.enrichment_produced
    assert "tax_treatment" not in p.consumer_injected
    assert "tax_treatment" not in p.db_generated
    assert "tax_treatment" not in p.compute_owned
    assert "tax_treatment" not in p.mapping_produced
    # currency STAYS mapping-produced (the lib overrides its value, not its origin).
    assert "currency" in mapping_produced_columns(StoreSkuCurrentPosition)
    assert "currency" not in enrichment_produced_columns(StoreSkuCurrentPosition)
