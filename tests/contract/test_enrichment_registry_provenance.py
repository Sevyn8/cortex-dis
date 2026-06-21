"""slice-5b cross-lib drift (§8b, load-bearing): the dis-enrichment registry and the
dis-validation provenance partitions are two code-owned declarations that MUST stay
consistent, or the consumer widens ``owned_columns`` with a column the canonical-shape
drift guard has not been told to admit — a silent gate hole. These fail loud.

The relationship is NOT naive equality: ``currency`` is a registered enrichment field
whose VALUE the lib overrides (D95), but its ORIGIN is still the mapping, so it stays
``mapping_produced``; ``tax_treatment`` genuinely originates in enrichment, so it is
``enrichment_produced``. import-linter forbids the two pure libs importing each other;
this test (outside both packages) is the agreement check.
"""

from __future__ import annotations

from dis_canonical import StoreSkuCurrentPosition
from dis_enrichment import CURRENT_POSITION, enrichment_fields
from dis_validation import enrichment_produced_columns, mapping_produced_columns


def test_every_enrichment_field_is_source_owned_for_the_hot_model() -> None:
    # The consumer widens owned_columns with enrichment_fields; the guard admits
    # mapping_produced ∪ enrichment_produced. Every enrichment field MUST be in that
    # union, or validate_post throws SuiteDriftError at runtime.
    fields = set(enrichment_fields(CURRENT_POSITION))
    source_owned = mapping_produced_columns(StoreSkuCurrentPosition) | enrichment_produced_columns(
        StoreSkuCurrentPosition
    )
    assert fields <= source_owned


def test_provenance_enrichment_partition_is_exactly_the_non_mapping_enrichment_fields() -> None:
    # Registry <-> provenance agreement: a registered field NOT produced by the mapping
    # (tax_treatment) MUST be classified enrichment_produced so the guard admits it; a
    # registered field the mapping DOES produce (currency — the lib overrides its value,
    # not its origin) STAYS mapping-produced and is NOT in enrichment_produced.
    fields = set(enrichment_fields(CURRENT_POSITION))
    mapping_produced = mapping_produced_columns(StoreSkuCurrentPosition)
    expected_enrichment = fields - mapping_produced
    assert set(enrichment_produced_columns(StoreSkuCurrentPosition)) == expected_enrichment
