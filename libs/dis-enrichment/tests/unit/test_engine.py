"""apply_enrichment behaviour (slice-5b; D94, D95): output-wins, row alignment,
the missing-vs-blank boundary (D97), and table-scope gating.

Expected values are hand-derived from the rule, never copied from the engine.
"""

from __future__ import annotations

import polars as pl
import pytest

from dis_core.errors import EnrichmentError
from dis_enrichment import CURRENT_POSITION, apply_enrichment


def _contribution() -> pl.DataFrame:
    # A snapshot-style contribution: currency is mapping-produced (the collision the
    # slice calls out); tax_treatment is NOT present (the lib creates it).
    return pl.DataFrame(
        {
            "sku_id": ["A", "B", "C"],
            "product_name": ["a", "b", "c"],
            "currency": ["USD", "USD", "USD"],  # mapping value — must be overridden
        }
    )


def test_output_wins_over_a_mapping_produced_column() -> None:
    result = apply_enrichment(
        _contribution(),
        {"currency": "INR", "tax_treatment": "INCLUSIVE"},
        table=CURRENT_POSITION,
    )
    # The store value wins on every row, not the mapping's "USD".
    assert result.contribution["currency"].to_list() == ["INR", "INR", "INR"]
    assert set(result.enriched_columns) == {"currency", "tax_treatment"}


def test_creates_a_not_yet_present_registered_column() -> None:
    result = apply_enrichment(
        _contribution(),
        {"currency": "INR", "tax_treatment": "EXCLUSIVE"},
        table=CURRENT_POSITION,
    )
    assert "tax_treatment" in result.contribution.columns
    assert result.contribution["tax_treatment"].to_list() == ["EXCLUSIVE"] * 3


def test_row_count_and_order_preserved() -> None:
    before = _contribution()
    result = apply_enrichment(
        before,
        {"currency": "INR", "tax_treatment": "INCLUSIVE"},
        table=CURRENT_POSITION,
    )
    assert result.contribution.height == before.height
    # Distinguishable, ordered rows — a reorder or row-count change would be caught.
    assert result.contribution["sku_id"].to_list() == ["A", "B", "C"]


def test_missing_registered_field_raises() -> None:
    with pytest.raises(EnrichmentError) as exc:
        apply_enrichment(_contribution(), {"currency": "INR"}, table=CURRENT_POSITION)
    assert exc.value.table == CURRENT_POSITION


def test_present_but_blank_is_written_through_not_raised() -> None:
    # D97 boundary (deferred): a present-but-blank value is written through this
    # slice, NOT a loud fail. Distinct from the missing-key contract violation.
    result = apply_enrichment(
        _contribution(),
        {"currency": "INR", "tax_treatment": None},
        table=CURRENT_POSITION,
    )
    assert result.contribution["tax_treatment"].to_list() == [None, None, None]


def test_unwired_table_is_a_noop() -> None:
    before = _contribution()
    result = apply_enrichment(before, {}, table="store_sku_sale_event")
    assert result.enriched_columns == ()
    assert result.contribution.equals(before)
