"""Source-shape suite tests — slice-05 criterion 4.

A passing chunk passes; a failing chunk fails with a typed, tenant-readable
reason — including the canonical example: "expected column `item_code`, got
`itemcd`".
"""

from __future__ import annotations

import polars as pl
import pytest

from dis_core.errors import DisError, SuiteDefinitionError
from dis_validation import ColumnExpectation, SourceShapeSuiteDef, run_source_shape


def test_passing_chunk_passes() -> None:
    definition = SourceShapeSuiteDef.from_rename({"item_code": "sku_id", "qty": "quantity"})
    chunk = pl.DataFrame({"item_code": ["a", "b"], "qty": ["1", "2"]})
    result = run_source_shape(definition, chunk)
    assert result.passed
    assert result.failures == ()


def test_missing_expected_column_yields_tenant_readable_reason() -> None:
    definition = SourceShapeSuiteDef.from_rename({"item_code": "sku_id", "qty": "quantity"})
    chunk = pl.DataFrame({"itemcd": ["a"], "qty": ["1"]})  # tenant renamed the column
    result = run_source_shape(definition, chunk)
    assert not result.passed
    reasons = [failure.reason for failure in result.failures]
    # The slice's canonical example, verbatim shape: both names in one reason.
    assert "expected column 'item_code', got 'itemcd'" in reasons
    assert result.failures[0].check == "column_in_dataframe"


def test_expected_columns_derive_from_rename_keys() -> None:
    definition = SourceShapeSuiteDef.from_rename({"item_code": "sku_id", "qty": "quantity"})
    assert [column.name for column in definition.expected_columns] == ["item_code", "qty"]


def test_extra_columns_tolerated_by_default_strict_when_declared() -> None:
    definition = SourceShapeSuiteDef.from_rename({"item_code": "sku_id"})
    chunk = pl.DataFrame({"item_code": ["a"], "surprise": ["x"]})
    assert run_source_shape(definition, chunk).passed

    strict_def = SourceShapeSuiteDef.from_rename({"item_code": "sku_id"}, allow_extra_columns=False)
    result = run_source_shape(strict_def, chunk)
    assert not result.passed
    assert any(failure.check == "column_in_schema" for failure in result.failures)
    assert any("unexpected column 'surprise'" in failure.reason for failure in result.failures)


def test_declared_pattern_plausibility_fails_with_row_grain() -> None:
    definition = SourceShapeSuiteDef(
        expected_columns=(ColumnExpectation(name="item_code", pattern=r"^SKU-\d+$"),)
    )
    chunk = pl.DataFrame({"item_code": ["SKU-1", "garbage"]})
    result = run_source_shape(definition, chunk)
    assert not result.passed
    failure = result.failures[0]
    assert failure.column == "item_code"
    assert failure.row_index == 1
    assert failure.value == "garbage"


def test_null_fraction_bound() -> None:
    definition = SourceShapeSuiteDef(
        expected_columns=(ColumnExpectation(name="item_code", max_null_fraction=0.5),)
    )
    ok_chunk = pl.DataFrame({"item_code": ["a", None]})
    assert run_source_shape(definition, ok_chunk).passed

    bad_chunk = pl.DataFrame({"item_code": [None, None, "a"]})
    result = run_source_shape(definition, bad_chunk)
    assert not result.passed
    assert any("more nulls than the declared bound" in failure.reason for failure in result.failures)


def test_row_count_bounds() -> None:
    definition = SourceShapeSuiteDef.from_rename({"item_code": "sku_id"}, min_rows=2, max_rows=3)
    too_few = pl.DataFrame({"item_code": ["a"]})
    result = run_source_shape(definition, too_few)
    assert not result.passed
    assert any("row count is implausible" in failure.reason for failure in result.failures)

    just_right = pl.DataFrame({"item_code": ["a", "b"]})
    assert run_source_shape(definition, just_right).passed


def test_empty_definition_fails_loud() -> None:
    with pytest.raises(SuiteDefinitionError, match="non-empty"):
        SourceShapeSuiteDef(expected_columns=())


def test_overrides_must_name_rename_keys() -> None:
    with pytest.raises(SuiteDefinitionError, match="absent from the rename map"):
        SourceShapeSuiteDef.from_rename(
            {"item_code": "sku_id"},
            overrides={"not_there": ColumnExpectation(name="not_there")},
        )


def test_definition_errors_are_dis_errors() -> None:
    with pytest.raises(DisError):
        SourceShapeSuiteDef(expected_columns=(ColumnExpectation(name="x", max_null_fraction=2.0),))
