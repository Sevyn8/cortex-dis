"""Layer (a): the deterministic CSV profiler, no LLM, fully reproducible.

HONESTY NOTE: the retail fixture CSVs use CLEAN canonical snake_case headers, so
this layer does NOT exercise LLM name-normalization. A green layer (a) proves
structural profiling on clean headers (base type, nullability, enum candidates,
header-union, presence/sample-size) and the capacity-bucketing function on known
inputs. Name-normalization from messy headers is layer (b) (test_proposer.py).

Capacity (varchar length, numeric precision/scale) is a PROPOSAL at the documented
default and is asserted here against KNOWN constructed inputs (the bucketing
function), NOT against the hand-authored retail IR fixture: capacity is declared
schema headroom, not data-inferable, so it is ratified by a human in A4. The retail
CSVs are realistic, not a capacity-probe matrix.
"""

from __future__ import annotations

from pathlib import Path

from dis_ui_server.infer.profiler import (
    ProfiledColumn,
    bucket_decimal_capacity,
    bucket_str_capacity,
    profile_csvs,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CSV_DIR = _REPO_ROOT / "tools" / "codegen" / "tests" / "fixtures" / "retail_csvs"
_CSVS = [_CSV_DIR / "posdump_acme.csv", _CSV_DIR / "inventory_store12.csv"]

# The 28 mapping-produced columns of retail current_position and their base family.
_EXPECTED_BASE: dict[str, str] = {
    "sku_id": "str",
    "sku_variant": "str",
    "sku_lot_batch": "str",
    "barcode": "str",
    "product_name": "str",
    "product_description": "str",
    "product_category": "str",
    "product_sub_category": "str",
    "product_department": "str",
    "supplier_id": "str",
    "packaging_type": "str",
    "unit_of_measure": "str",
    "promo_identifier": "str",
    "currency": "str",
    "expiry_source": "str",
    "regulatory_type": "str",
    "sku_status": "str",
    "sku_size": "decimal",
    "current_retail_price": "decimal",
    "unit_cost": "decimal",
    "promo_price": "decimal",
    "stock_qty": "decimal",
    "expiry_confidence": "decimal",
    "reorder_point": "decimal",
    "lead_time_days": "int",
    "expiry_date": "date",
    "receipt_date": "date",
    "regulatory_flag": "bool",
}
# Only these four are present in BOTH files with no null cells -> nullable False.
_NOT_NULLABLE = {"sku_id", "product_name", "current_retail_price", "currency"}


def _profiled() -> dict[str, ProfiledColumn]:
    return {c.name: c for c in profile_csvs(_CSVS)}


def test_header_union_is_exactly_the_28_mapping_produced_columns() -> None:
    by = _profiled()
    assert set(by) == set(_EXPECTED_BASE)


def test_base_type_family_per_column() -> None:
    by = _profiled()
    for name, expected in _EXPECTED_BASE.items():
        assert by[name].base == expected, f"{name}: expected base {expected}, got {by[name].base}"


def test_weak_nullable_from_null_presence_or_partial_file_presence() -> None:
    by = _profiled()
    for name in _EXPECTED_BASE:
        expected = name not in _NOT_NULLABLE
        assert by[name].nullable is expected, f"{name}: expected nullable {expected}"


def test_enum_candidate_distinct_value_sets() -> None:
    by = _profiled()
    # The sample misses CV_DETECTED (representativeness): A3 proposes the OBSERVED set,
    # a flagged candidate the human widens in A4.
    assert by["expiry_source"].distinct_values == ("ESTIMATED", "PRINTED", "SCANNED")
    assert by["currency"].distinct_values == ("EUR", "USD")
    # A high-cardinality column carries no enum candidate.
    assert by["product_description"].distinct_values == ()


def test_presence_and_sample_size_stamps() -> None:
    by = _profiled()
    # shared columns: present in both files (2/2), 4 + 4 = 8 rows profiled.
    assert (by["sku_id"].present_in_files, by["sku_id"].total_files, by["sku_id"].rows_profiled) == (2, 2, 8)
    # File-A-only column: 1/2, 4 rows.
    assert (by["barcode"].present_in_files, by["barcode"].total_files, by["barcode"].rows_profiled) == (
        1,
        2,
        4,
    )
    # File-B-only column: 1/2, 4 rows.
    assert (by["stock_qty"].present_in_files, by["stock_qty"].total_files, by["stock_qty"].rows_profiled) == (
        1,
        2,
        4,
    )


def test_source_headers_recorded() -> None:
    by = _profiled()
    assert by["sku_id"].source_headers == ("sku_id",)


def test_str_capacity_bucketing_function_on_known_inputs() -> None:
    # Smallest standard varchar capacity >= observed max (clamp 256).
    assert bucket_str_capacity(10) == 32
    assert bucket_str_capacity(32) == 32
    assert bucket_str_capacity(40) == 64
    assert bucket_str_capacity(100) == 128
    assert bucket_str_capacity(200) == 256
    assert bucket_str_capacity(500) == 256


def test_decimal_capacity_bucketing_function_on_known_inputs() -> None:
    # Smallest standard (precision, scale) with both >= observed.
    assert bucket_decimal_capacity(4, 2) == (5, 2)  # e.g. 19.99
    assert bucket_decimal_capacity(2, 2) == (3, 2)  # e.g. 0.95
    assert bucket_decimal_capacity(8, 3) == (8, 3)
    # smallest in (precision, scale) order with both >= observed: (12,4) precedes (14,3)
    assert bucket_decimal_capacity(11, 3) == (12, 4)
    assert bucket_decimal_capacity(13, 4) == (14, 4)
