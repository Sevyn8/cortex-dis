"""Unit tests for the mechanical fallback matcher (pure, no I/O)."""

from __future__ import annotations

from dis_ui_server.catalog import build_field_catalog
from dis_ui_server.schemas.mapping_suggestions import ColumnProfile
from dis_ui_server.suggest.fallback_matcher import match_columns

CATALOG = build_field_catalog()
CATALOG_KEYS = {field.key for field in CATALOG}


def _profile(name: str, datatype: str = "text") -> ColumnProfile:
    return ColumnProfile(name=name, inferred_datatype=datatype, null_pct=0.0, sample_values=["x"])


def test_matches_common_retail_columns_with_high_confidence() -> None:
    out = match_columns(
        [_profile("qty", "integer"), _profile("item_code", "text")],
        CATALOG,
    )
    by_col = {s.source_column: s for s in out}
    assert by_col["qty"].suggested_target == "quantity"
    assert by_col["qty"].confidence >= 0.7
    assert by_col["item_code"].suggested_target == "sku_id"
    assert by_col["item_code"].confidence >= 0.7


def test_unknown_column_gets_low_confidence_but_a_valid_target() -> None:
    [out] = match_columns([_profile("zzz_unrelated_blob")], CATALOG)
    assert out.confidence < 0.5  # very-low / needs-review band
    # Always a real catalog key so the UI target select has a matching option.
    assert out.suggested_target in CATALOG_KEYS


def test_suggestions_are_always_real_catalog_keys_and_carry_no_reasoning() -> None:
    out = match_columns(
        [_profile("qty", "integer"), _profile("txn_date", "datetime"), _profile("mystery")],
        CATALOG,
    )
    assert len(out) == 3
    for s in out:
        assert s.suggested_target in CATALOG_KEYS
        # The mechanical matcher never fabricates reasoning or alternatives.
        assert s.reasoning is None
        assert s.alternatives is None


def test_empty_catalog_yields_null_target() -> None:
    [out] = match_columns([_profile("qty", "integer")], [])
    assert out.suggested_target is None
    assert out.confidence == 0.0
