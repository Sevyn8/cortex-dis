"""The enrichment registry shape + the table-scope seam (slice-5b; D95).

Expected values are hand-derived from the slice contract (currency + tax_treatment
on the current-position table, both resolving from identity_mirror.stores), not
read from the code under test.
"""

from __future__ import annotations

from dis_enrichment import CURRENT_POSITION, ENRICHMENT_REGISTRY, EnrichmentField, enrichment_fields


def test_current_position_registers_currency_and_tax_treatment() -> None:
    assert set(enrichment_fields(CURRENT_POSITION)) == {"currency", "tax_treatment"}


def test_unwired_table_has_no_registered_fields() -> None:
    # The event/history tables are not wired this slice; the seam returns empty so
    # the engine no-ops there.
    assert enrichment_fields("store_sku_sale_event") == ()
    assert enrichment_fields("not_a_table") == ()


def test_each_field_records_its_source_and_table_scope() -> None:
    by_name = {f.canonical_field: f for f in ENRICHMENT_REGISTRY}
    assert by_name.keys() == {"currency", "tax_treatment"}
    for field in ENRICHMENT_REGISTRY:
        assert isinstance(field, EnrichmentField)
        # Source-agnostic identity: the source is recorded (mechanism), but it is
        # the store this slice.
        assert field.source == "identity_mirror.stores"
        assert field.source_field == field.canonical_field
        assert CURRENT_POSITION in field.tables
