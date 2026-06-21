"""Product-rule-to-mechanism traces across both pipeline libs (slice-05 adversarial pass).

The two gates carry OPPOSITE column postures BY DESIGN and must not be confused:
the source-shape gate is permissive about extra columns (real tenant files carry
them; D13), while the canonical-shape gate is strict (an off-universe column in a
contribution fails loud). Each scenario here is a product rule traced end-to-end,
not a unit assertion on one function.

Pure cross-lib contract tests: no stack, no DB — they run under bare pytest.
"""

from __future__ import annotations

import polars as pl

from dis_canonical import StoreSkuCurrentPosition
from dis_mapping import SourceMapping, apply_mapping
from dis_validation import (
    CanonicalShapeSuiteDef,
    SourceShapeSuiteDef,
    run_canonical_shape,
    run_source_shape,
)

_MAPPING = SourceMapping.model_validate(
    {
        "version": 1,
        "rename": {
            "item_code": "sku_id",
            "name": "product_name",
            "cat": "product_category",
            "rrp": "current_retail_price",
            "cost": "unit_cost",
            "curr": "currency",
            "desc": "product_description",
        },
        "normalize": {
            "current_retail_price": [
                {
                    "op": "parse_decimal",
                    "args": {"decimal_separator": ".", "thousands_separator": None},
                }
            ],
            "unit_cost": [
                {
                    "op": "parse_decimal",
                    "args": {"decimal_separator": ".", "thousands_separator": None},
                }
            ],
        },
        "cast": {
            "current_retail_price": {"type": "decimal", "precision": 12, "scale": 4},
            "unit_cost": {"type": "decimal", "precision": 12, "scale": 4},
        },
        "derive": {},
    }
)

_OWNED = (
    "sku_id",
    "product_name",
    "product_category",
    "current_retail_price",
    "unit_cost",
    "currency",
    "product_description",
)


def _real_csv() -> pl.DataFrame:
    """A realistic tenant CSV: extra unmapped columns + an entirely-empty optional one."""
    return pl.DataFrame(
        {
            "item_code": ["SKU-1", "SKU-2"],
            "name": ["Cola 500ml", "Chips 90g"],
            "cat": ["BEVERAGE", "SNACKS"],
            "rrp": ["45.00", "20.00"],
            "cost": ["30.00", "12.00"],
            "curr": ["INR", "INR"],
            "desc": pl.Series([None, None], dtype=pl.String),
            "store_manager_notes": ["fine", "ok"],
            "erp_internal_code": ["X1", "X2"],
        }
    )


def test_real_csv_with_extra_and_empty_optional_columns_passes_source_shape() -> None:
    # Product rule: real files carry unmapped columns and empty optionals; the
    # pre-mapping gate must tolerate them (permissive posture, D13).
    definition = SourceShapeSuiteDef.from_rename(_MAPPING.rename)
    assert run_source_shape(definition, _real_csv()).passed


def test_the_two_gates_hold_opposite_column_postures_at_once() -> None:
    # The SAME pipeline: permissive at source-shape, strict at canonical-shape.
    src_def = SourceShapeSuiteDef.from_rename(_MAPPING.rename)
    chunk = _real_csv()
    assert run_source_shape(src_def, chunk).passed  # extras tolerated here...

    contribution = apply_mapping(_MAPPING, chunk).contribution
    c_def = CanonicalShapeSuiteDef(target_model=StoreSkuCurrentPosition, owned_columns=_OWNED)
    assert run_canonical_shape(c_def, contribution).passed

    poisoned = contribution.with_columns(pl.lit("oops").alias("tenant_id"))
    result = run_canonical_shape(c_def, poisoned)  # ...but NOTHING extra here.
    assert not result.passed
    assert any(f.check == "column_in_schema" for f in result.failures)


def test_headers_only_file_errors_loud_with_handed_in_bound() -> None:
    # Product rule: an empty / headers-only upload must error, not pass silently.
    # The bound producing the failure is the suite definition's (min_rows),
    # handed in by the caller — not a branch in lib code.
    definition = SourceShapeSuiteDef.from_rename(_MAPPING.rename)
    assert definition.min_rows == 1  # the declarable bound this failure comes from
    result = run_source_shape(definition, _real_csv().head(0))
    assert not result.passed
    assert any("row count is implausible" in f.reason for f in result.failures)


def test_all_null_mandatory_field_fails_loud_at_canonical_shape() -> None:
    # Product rule: a mapped, mandatory canonical column that arrives entirely
    # null must fail loudly — and "mandatory" is DERIVED from the canonical
    # model's nullability (product_name is NOT NULL on the live table), never a
    # hard-coded column list. Full trace: rename -> normalize -> cast produce a
    # contribution with null cells (a null is NOT a per-cell failure), then the
    # canonical-shape gate rejects each row naming the column.
    chunk = _real_csv().with_columns(pl.lit(None, dtype=pl.String).alias("name"))
    mapped = apply_mapping(_MAPPING, chunk)
    assert mapped.failures == ()  # nulls flow through; they are not cell failures
    assert mapped.contribution.height == 2

    c_def = CanonicalShapeSuiteDef(target_model=StoreSkuCurrentPosition, owned_columns=_OWNED)
    result = run_canonical_shape(c_def, mapped.contribution)
    assert not result.passed
    name_failures = [f for f in result.failures if f.column == "product_name"]
    assert {f.row_index for f in name_failures} == {0, 1}  # per-row grain
    assert all("required but arrived empty" in f.reason for f in name_failures)
    # The empty OPTIONAL column (product_description, nullable per model) is fine:
    assert not any(f.column == "product_description" for f in result.failures)
