"""Chunk accounting — every input row exactly once (slice-05 adversarial pass).

The consumer's deferred X% threshold (B2, Slice 10) is only computable if the
engine accounts for EVERY input row exactly once: either in the contribution
(locatable via ``source_row_indices``) or carrying at least one failure — never
both, never neither. A silently dropped row (swallowed exception, a filter losing
a row without a recorded failure) would corrupt the downstream threshold math.

Also pinned here: the threshold is on ROWS, not cells (a row with three bad cells
is ONE bad row, computable from ``failed_row_indices``); and the engine is
deterministic (same input -> identical contribution, row order, column order,
failures — the lib is pure, so nothing else is acceptable).
"""

from __future__ import annotations

import polars as pl

from dis_mapping import SourceMapping, apply_mapping


def _mixed_mapping() -> SourceMapping:
    return SourceMapping.model_validate(
        {
            "version": 1,
            "rename": {"itemcd": "sku_id", "price": "unit_cost", "sold_on": "expiry_date"},
            "normalize": {
                "sku_id": [
                    {"op": "normalize_whitespace", "args": {}},
                    {"op": "normalize_case", "args": {"mode": "upper"}},
                ],
                "unit_cost": [
                    {"op": "null_tokens", "args": {"tokens": ["N/A"]}},
                    {
                        "op": "parse_decimal",
                        "args": {"decimal_separator": ".", "thousands_separator": None},
                    },
                ],
                "expiry_date": [{"op": "parse_date", "args": {"format": "%d-%m-%Y"}}],
            },
            "cast": {
                "unit_cost": {"type": "decimal", "precision": 12, "scale": 4},
                "expiry_date": {"type": "date"},
            },
            "derive": {"currency": [{"op": "constant", "args": {"value": "INR"}}]},
        }
    )


def _mixed_chunk() -> pl.DataFrame:
    # Six rows exercising every accounting class:
    # 0: clean. 1: one bad cell (date). 2: TWO bad cells in one row (price + date).
    # 3: declared null (N/A -> null, NOT a failure). 4: clean. 5: bad price only.
    return pl.DataFrame(
        {
            "itemcd": ["  a ", "b", "c", "d", "e", "f"],
            "price": ["1.5", "2.0", "oops", "N/A", "3.25", "bad"],
            "sold_on": [
                "01-01-2026",
                "not-a-date",
                "also-bad",
                "02-01-2026",
                "03-01-2026",
                "04-01-2026",
            ],
        }
    )


def test_every_input_row_is_contributed_xor_failed_exactly_once() -> None:
    result = apply_mapping(_mixed_mapping(), _mixed_chunk())
    n = _mixed_chunk().height

    contributed = set(result.source_row_indices)
    failed = set(result.failed_row_indices)

    # Partition: disjoint, covering, exact.
    assert contributed & failed == set(), "a row must never be both contributed and failed"
    assert contributed | failed == set(range(n)), "a row must never vanish unaccounted"
    assert len(result.source_row_indices) == result.contribution.height
    assert result.contribution.height + len(failed) == n

    # The concrete expectation for this chunk: rows 1, 2, 5 failed; 0, 3, 4 contributed.
    assert failed == {1, 2, 5}
    assert result.source_row_indices == (0, 3, 4)
    # Row 3's declared null contributed as a null cell (declaration, not failure).
    assert result.contribution["unit_cost"].to_list()[1] is None


def test_threshold_grain_is_rows_not_cells() -> None:
    result = apply_mapping(_mixed_mapping(), _mixed_chunk())
    # Row 2 carries TWO cell failures but is ONE bad row.
    row2_failures = [f for f in result.failures if f.row_index == 2]
    assert len(row2_failures) == 2
    assert result.failed_row_indices.count(2) == 1
    # Distinct-bad-rows is directly computable and unambiguous:
    assert len(result.failed_row_indices) == 3
    # ...so the consumer's row-grain math closes exactly:
    assert len(result.failed_row_indices) + result.contribution.height == _mixed_chunk().height


def test_engine_is_deterministic_and_does_not_mutate_its_input() -> None:
    mapping = _mixed_mapping()
    chunk = _mixed_chunk()
    before = chunk.clone()

    first = apply_mapping(mapping, chunk)
    second = apply_mapping(mapping, chunk)

    # Identical contribution: same rows, same row order, same column order.
    assert first.contribution.equals(second.contribution)
    assert first.contribution.columns == second.contribution.columns
    assert first.contribution.columns == list(mapping.target_columns)  # declared order
    assert first.source_row_indices == second.source_row_indices
    # Identical failures, identical order.
    assert first.failures == second.failures
    # The input chunk is untouched (pure function over (mapping, chunk)).
    assert chunk.equals(before)
