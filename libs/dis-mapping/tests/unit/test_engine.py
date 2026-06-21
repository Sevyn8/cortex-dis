"""Engine tests — slice-05 acceptance criteria 2 and 3 plus the engine contract.

Criterion 2: the rename -> normalize -> cast -> derive ordering with
normalize-before-cast proven LOAD-BEARING (a cast-first path fails the exact input
the full path passes); ordered multi-transform lists applied in declared order;
consumer-injected columns absent from the output.

Criterion 3: per-cell failure grain carrying column/value/expected format and the
failing transform's op + transform_index; a row with any failed cell yields NO
contribution (whole-row drop, no nulled-cell pass-through).

Review-only (stated, not asserted green): that the engine applies no
pass-threshold and routes nothing is the ABSENCE of behaviour (B2, Slice 10's);
a test cannot prove absence — held by review and the import-linter/no-I/O
contracts (root tests/contract/).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import polars as pl
import pytest

from dis_core.errors import MappingInputError
from dis_mapping import LogContext, SourceMapping, apply_mapping
from dis_mapping.engine.cast import run_cast
from dis_mapping.result import MappingResult


def _mapping(**overrides: Any) -> SourceMapping:
    base: dict[str, Any] = {
        "version": 1,
        "rename": {"itemcd": "sku_id", "price": "unit_cost"},
        "normalize": {},
        "cast": {},
        "derive": {},
    }
    base.update(overrides)
    return SourceMapping.model_validate(base)


# -- Criterion 2: ordering ---------------------------------------------------------


def test_cast_first_fails_the_comma_decimal_that_normalize_then_cast_passes() -> None:
    """The normalize-before-cast ordering is load-bearing, not stylistic (D20).

    Premise enforced first: a cast-first path on the raw comma-decimal MUST fail —
    if polars ever started tolerating ',' decimals, this assertion (not the
    ordering) would be what goes red, keeping the proof honest.
    """
    mapping = _mapping(
        normalize={
            "unit_cost": [
                {
                    "op": "parse_decimal",
                    "args": {"decimal_separator": ",", "thousands_separator": None},
                }
            ]
        },
        cast={"unit_cost": {"type": "decimal", "precision": 12, "scale": 4}},
    )
    raw = pl.DataFrame({"itemcd": ["a"], "price": ["23,45"]})

    # Premise: cast DIRECTLY on the un-normalized frame produces a per-cell failure.
    cast_first_failures: list[Any] = []
    renamed = raw.select(["itemcd", "price"]).rename({"itemcd": "sku_id", "price": "unit_cost"})
    run_cast(renamed, mapping.cast, {}, cast_first_failures)
    assert len(cast_first_failures) == 1, "cast-first path tolerated a comma decimal"
    assert cast_first_failures[0].stage == "cast"
    assert cast_first_failures[0].value == "23,45"

    # Full engine (normalize THEN cast): the same input passes cleanly.
    result = apply_mapping(mapping, raw)
    assert result.failures == ()
    assert result.contribution["unit_cost"].to_list() == [Decimal("23.4500")]


def test_multi_transform_list_applies_in_declared_order() -> None:
    """whitespace-then-upper on one column; reversed declaration differs in effect."""
    trim_then_upper = _mapping(
        normalize={
            "sku_id": [
                {"op": "normalize_whitespace", "args": {}},
                {"op": "normalize_case", "args": {"mode": "upper"}},
            ]
        }
    )
    chunk = pl.DataFrame({"itemcd": ["  ab-1 "], "price": ["1"]})
    result = apply_mapping(trim_then_upper, chunk)
    assert result.contribution["sku_id"].to_list() == ["AB-1"]

    # Order-significance: map_enum (exact-match) before vs after normalize_case.
    # upper-then-enum maps; enum-then-upper fails the lookup — same ops, different
    # declared order, observably different outcome.
    upper_then_enum = _mapping(
        normalize={
            "sku_id": [
                {"op": "normalize_case", "args": {"mode": "upper"}},
                {"op": "map_enum", "args": {"mapping": {"AB": "ALPHA-BRAVO"}}},
            ]
        }
    )
    enum_then_upper = _mapping(
        normalize={
            "sku_id": [
                {"op": "map_enum", "args": {"mapping": {"AB": "ALPHA-BRAVO"}}},
                {"op": "normalize_case", "args": {"mode": "upper"}},
            ]
        }
    )
    lowercase_chunk = pl.DataFrame({"itemcd": ["ab"], "price": ["1"]})
    ok = apply_mapping(upper_then_enum, lowercase_chunk)
    assert ok.failures == ()
    assert ok.contribution["sku_id"].to_list() == ["ALPHA-BRAVO"]

    reversed_result = apply_mapping(enum_then_upper, lowercase_chunk)
    assert len(reversed_result.failures) == 1
    assert reversed_result.failures[0].op == "map_enum"
    assert reversed_result.failures[0].transform_index == 0


def test_empty_transform_list_passes_column_through_unchanged() -> None:
    mapping = _mapping(normalize={"sku_id": []})
    chunk = pl.DataFrame({"itemcd": ["  raw "], "price": ["1"]})
    result = apply_mapping(mapping, chunk)
    assert result.contribution["sku_id"].to_list() == ["  raw "]


def test_contribution_carries_mapping_targets_only_and_never_injected_columns() -> None:
    """The partial-contribution invariant (criterion 2, D8, hard rule 5)."""
    mapping = _mapping(derive={"currency": [{"op": "constant", "args": {"value": "INR"}}]})
    chunk = pl.DataFrame({"itemcd": ["a"], "price": ["1"], "unmapped_extra": ["ignored"]})
    result = apply_mapping(mapping, chunk)

    # Exactly the mapping's target set, in declaration order...
    assert result.contribution.columns == ["sku_id", "unit_cost", "currency"]
    # ...and the consumer-injected columns are absent even if the target-set
    # assertion were ever loosened.
    injected = {"tenant_id", "store_id", "trace_id", "mapping_version_id"}
    assert injected & set(result.contribution.columns) == set()


# -- Criterion 3: per-cell failure, whole-row drop ----------------------------------


def test_per_cell_failure_carries_context_and_partial_row_yields_nothing() -> None:
    mapping = _mapping(
        rename={"itemcd": "sku_id", "sold_on": "expiry_date"},
        normalize={"expiry_date": [{"op": "parse_date", "args": {"format": "%d-%m-%Y"}}]},
        cast={"expiry_date": {"type": "date"}},
    )
    chunk = pl.DataFrame(
        {
            "itemcd": ["a", "b", "c"],
            "sold_on": ["01-01-2026", "not-a-date", "02-01-2026"],
        }
    )
    result = apply_mapping(mapping, chunk)

    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.row_index == 1
    assert failure.column == "expiry_date"
    assert failure.source_column == "sold_on"
    assert failure.value == "not-a-date"
    assert failure.expected_format == "date in format '%d-%m-%Y'"
    assert failure.stage == "normalize"

    # Whole-row drop: row 1's VALID cells (sku_id "b") appear nowhere.
    assert result.contribution.height == 2
    assert result.source_row_indices == (0, 2)
    assert "b" not in result.contribution["sku_id"].to_list()
    assert result.failed_row_indices == (1,)


def test_failure_in_multi_transform_list_attributes_the_failing_step() -> None:
    """A value surviving step 0 and failing step 1 reports op + transform_index=1."""
    mapping = _mapping(
        normalize={
            "unit_cost": [
                {"op": "null_tokens", "args": {"tokens": ["N/A"]}},
                {
                    "op": "parse_decimal",
                    "args": {"decimal_separator": ".", "thousands_separator": None},
                },
            ]
        }
    )
    chunk = pl.DataFrame({"itemcd": ["a", "b", "c"], "price": ["N/A", "12.5", "abc"]})
    result = apply_mapping(mapping, chunk)

    # "N/A" was DECLARED null (step 0) — a null, not a failure; "abc" fails step 1.
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.row_index == 2
    assert failure.op == "parse_decimal"
    assert failure.transform_index == 1
    assert failure.value == "abc"
    # The declared-null row contributes (null cell, no failure).
    assert result.source_row_indices == (0, 1)
    assert result.contribution["unit_cost"].to_list() == [None, "12.5"]


def test_failed_cell_skips_remaining_steps_one_failure_per_cell() -> None:
    """A cell failing at step k is reported once; later steps do not re-fail it."""
    mapping = _mapping(
        normalize={
            "unit_cost": [
                {
                    "op": "parse_decimal",
                    "args": {"decimal_separator": ".", "thousands_separator": None},
                },
                {"op": "map_enum", "args": {"mapping": {"1": "ONE"}}},
            ]
        }
    )
    chunk = pl.DataFrame({"itemcd": ["a"], "price": ["bogus"]})
    result = apply_mapping(mapping, chunk)
    assert len(result.failures) == 1
    assert result.failures[0].op == "parse_decimal"
    assert result.failures[0].transform_index == 0


def test_cast_failure_after_clean_normalize_is_reported_at_cast_stage() -> None:
    # No normalize rules: the raw value reaches cast directly and fails there.
    mapping = _mapping(cast={"unit_cost": {"type": "integer"}})
    chunk = pl.DataFrame({"itemcd": ["a", "b"], "price": ["12", "12.5"]})
    result = apply_mapping(mapping, chunk)
    assert len(result.failures) == 1
    assert result.failures[0].stage == "cast"
    assert result.failures[0].op == "cast"
    assert result.failures[0].row_index == 1
    assert result.contribution["unit_cost"].to_list() == [12]


def test_multiple_failed_cells_in_one_row_each_reported_row_dropped_once() -> None:
    mapping = _mapping(
        normalize={
            "unit_cost": [
                {
                    "op": "parse_decimal",
                    "args": {"decimal_separator": ".", "thousands_separator": None},
                }
            ],
            "sku_id": [{"op": "map_enum", "args": {"mapping": {"known": "KNOWN"}}}],
        }
    )
    chunk = pl.DataFrame({"itemcd": ["unknown"], "price": ["bogus"]})
    result = apply_mapping(mapping, chunk)
    assert {(f.column, f.row_index) for f in result.failures} == {
        ("unit_cost", 0),
        ("sku_id", 0),
    }
    assert result.contribution.height == 0
    assert result.source_row_indices == ()


def test_all_rows_failing_yields_empty_contribution_with_schema() -> None:
    mapping = _mapping(
        normalize={
            "unit_cost": [
                {
                    "op": "parse_decimal",
                    "args": {"decimal_separator": ".", "thousands_separator": None},
                }
            ]
        },
        cast={"unit_cost": {"type": "decimal", "precision": 12, "scale": 4}},
    )
    chunk = pl.DataFrame({"itemcd": ["a"], "price": ["x"]})
    result = apply_mapping(mapping, chunk)
    assert result.contribution.height == 0
    assert result.contribution.columns == ["sku_id", "unit_cost"]


# -- Caller contract ----------------------------------------------------------------


def test_missing_declared_source_column_raises_mapping_input_error() -> None:
    mapping = _mapping()
    chunk = pl.DataFrame({"itemcd": ["a"]})  # no 'price'
    with pytest.raises(MappingInputError, match="missing source column"):
        apply_mapping(mapping, chunk)


def test_typed_column_with_normalize_rules_raises_mapping_input_error() -> None:
    mapping = _mapping(normalize={"unit_cost": [{"op": "normalize_whitespace", "args": {}}]})
    chunk = pl.DataFrame({"itemcd": ["a"], "price": [1.5]})  # already Float64
    with pytest.raises(MappingInputError, match="normalize operates on strings"):
        apply_mapping(mapping, chunk)


def test_already_typed_column_with_matching_cast_passes_through() -> None:
    mapping = _mapping(cast={"unit_cost": {"type": "integer"}})
    chunk = pl.DataFrame({"itemcd": ["a"], "price": [12]})  # already Int64
    result = apply_mapping(mapping, chunk)
    assert result.failures == ()
    assert result.contribution["unit_cost"].to_list() == [12]


# -- Logging discipline (criterion 7) -------------------------------------------------


def test_failure_logging_binds_context_and_never_carries_cell_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mapping = _mapping(
        normalize={
            "unit_cost": [
                {
                    "op": "parse_decimal",
                    "args": {"decimal_separator": ".", "thousands_separator": None},
                }
            ]
        }
    )
    secret_value = "SECRET-PAYLOAD-1299x"
    chunk = pl.DataFrame({"itemcd": ["a"], "price": [secret_value]})
    with caplog.at_level(logging.DEBUG, logger="dis-mapping"):
        result = apply_mapping(mapping, chunk, log_context=LogContext(tenant_id="ten-1", trace_id="tr-1"))

    # The failure OBJECT carries the value (the D20 quarantine payload)...
    assert result.failures[0].value == secret_value
    # ...but no log line ever does (never log PII / raw payloads).
    assert caplog.records, "expected a failure log line"
    for record in caplog.records:
        assert secret_value not in record.getMessage()
        assert secret_value not in str(getattr(record, "__dict__", {}))
        assert record.__dict__.get("tenant_id") == "ten-1"
        assert record.__dict__.get("trace_id") == "tr-1"
        assert record.__dict__.get("service") == "dis-mapping"
        assert record.__dict__.get("stage") == "mapping"


def test_result_type_shape() -> None:
    mapping = _mapping()
    chunk = pl.DataFrame({"itemcd": ["a"], "price": ["1"]})
    result = apply_mapping(mapping, chunk)
    assert isinstance(result, MappingResult)
    assert result.source_row_indices == (0,)
    assert result.failures == ()
