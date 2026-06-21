"""Canonical-shape suite tests — slice-05 criteria 5 and 6 (suite side).

Criterion 5: field set / dtype / nullability derived from ONE named dis-canonical
model restricted to the source-owned columns; authored business invariants fail
per-row; consumer-injected columns are excluded by construction. No
``identity_mirror`` existence check exists anywhere in this lib (a DB read a pure
lib cannot do) — held structurally by the import-linter contracts plus review,
not by a unit assertion.

Criterion 6 (suite direction): the materialized suite's column set equals the
declared owned set; an owned set reaching outside the model's mapping-produced
universe errors; ``strict=True`` rejects off-universe columns in the data.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import polars as pl
import pytest
from pandera import Check

from dis_canonical import StoreSkuSaleEvent, StoreSkuSignalHistory
from dis_core.errors import SuiteDefinitionError, SuiteDriftError
from dis_validation import (
    CanonicalShapeSuiteDef,
    materialize_canonical_shape,
    run_canonical_shape,
    suite_column_set,
)

OWNED = ("sku_id", "event_subtype", "quantity", "unit_retail_price", "unit_sale_price")

_SCHEMA: dict[str, Any] = {
    "sku_id": pl.String,
    "event_subtype": pl.String,
    "quantity": pl.Decimal(14, 3),
    "unit_retail_price": pl.Decimal(12, 4),
    "unit_sale_price": pl.Decimal(12, 4),
}


def _definition(**overrides: Any) -> CanonicalShapeSuiteDef:
    base: dict[str, Any] = {
        "target_model": StoreSkuSaleEvent,
        "owned_columns": OWNED,
        "column_checks": {},
        "frame_checks": [
            Check(
                lambda data: data.lazyframe.select(pl.col("unit_sale_price") <= pl.col("unit_retail_price")),
                name="unit_sale_price<=unit_retail_price",
            )
        ],
    }
    base.update(overrides)
    return CanonicalShapeSuiteDef(**base)


def _contribution(**overrides: Any) -> pl.DataFrame:
    rows: dict[str, list[Any]] = {
        "sku_id": ["SKU-1"],
        "event_subtype": ["SALE"],
        "quantity": [Decimal("2.000")],
        "unit_retail_price": [Decimal("10.0000")],
        "unit_sale_price": [Decimal("9.0000")],
    }
    rows.update(overrides)
    return pl.DataFrame(rows, schema=_SCHEMA)


def test_passing_contribution_passes() -> None:
    result = run_canonical_shape(_definition(), _contribution())
    assert result.passed
    assert result.failures == ()


def test_authored_business_invariant_fails_per_row() -> None:
    contribution = _contribution(unit_sale_price=[Decimal("99.0000")])
    result = run_canonical_shape(_definition(), contribution)
    assert not result.passed
    failure = result.failures[0]
    assert failure.check == "unit_sale_price<=unit_retail_price"
    assert failure.row_index == 0
    assert failure.column is None  # cross-field, not one column's fault
    assert "cross-field invariant" in failure.reason


def test_field_shape_is_derived_from_the_model_not_hand_coded() -> None:
    schema = materialize_canonical_shape(_definition())
    # Nullability from is_required(): quantity is NOT NULL on the live table.
    assert schema.columns["quantity"].nullable is False
    # Dtype from the model's decimal metadata (numeric(14,3) -> Decimal(14,3)).
    assert schema.columns["quantity"].dtype.type == pl.Decimal(14, 3)
    # Literal vocab (CHECK constraint) becomes an isin check.
    contribution = _contribution(event_subtype=["NOT-A-SUBTYPE"])
    result = run_canonical_shape(_definition(), contribution)
    assert not result.passed
    assert any(f.column == "event_subtype" and "isin" in f.check for f in result.failures)


def test_strenum_vocab_derived_from_model_pg_enum() -> None:
    # The StrEnum deriver branch (pg enums), exercised via expiry_source on the hot
    # table — surfaced as untested by the adversarial pass after tax_treatment
    # (the other StrEnum) was reclassified consumer-injected.
    from dis_canonical import StoreSkuCurrentPosition

    definition = CanonicalShapeSuiteDef(
        target_model=StoreSkuCurrentPosition, owned_columns=("sku_id", "expiry_source")
    )
    schema = materialize_canonical_shape(definition)
    isin_checks = [c for c in schema.columns["expiry_source"].checks if "isin" in str(c)]
    assert isin_checks, "expiry_source should carry a model-derived isin check"

    good = pl.DataFrame({"sku_id": ["SKU-1"], "expiry_source": ["PRINTED"]})
    assert run_canonical_shape(definition, good).passed
    bad = pl.DataFrame({"sku_id": ["SKU-1"], "expiry_source": ["GUESSED"]})
    result = run_canonical_shape(definition, bad)
    assert not result.passed
    assert any(f.column == "expiry_source" and "isin" in f.check for f in result.failures)


def test_max_length_derived_from_model_string_constraints() -> None:
    # sku_id is varchar(128) on the live table -> StringConstraints(max_length=128).
    contribution = _contribution(sku_id=["x" * 129])
    result = run_canonical_shape(_definition(), contribution)
    assert not result.passed
    assert any(f.column == "sku_id" and "str_length" in f.check for f in result.failures)


def test_wrong_dtype_fails() -> None:
    contribution = pl.DataFrame(
        {
            "sku_id": ["SKU-1"],
            "event_subtype": ["SALE"],
            "quantity": ["2.000"],  # left as a string — cast stage skipped
            "unit_retail_price": [Decimal("10.0000")],
            "unit_sale_price": [Decimal("9.0000")],
        },
        schema={**_SCHEMA, "quantity": pl.String},
    )
    result = run_canonical_shape(_definition(), contribution)
    assert not result.passed
    assert any(f.column == "quantity" and "dtype" in f.check for f in result.failures)


def test_decimal_dtype_mismatch_failure_is_indistinguishable_from_native() -> None:
    """D50 condition: the synthesized Decimal dtype failure must match a NATIVE
    pandera dtype failure downstream — same type, same wording shape, same grain.
    Slice 10 and the quarantine console must never see two shapes for one logical
    error.
    """
    # Synthesized path: Decimal schema column vs String data (the D50 pre-check).
    decimal_mismatch = pl.DataFrame(
        {
            "sku_id": ["SKU-1"],
            "event_subtype": ["SALE"],
            "quantity": ["2.000"],
            "unit_retail_price": [Decimal("10.0000")],
            "unit_sale_price": [Decimal("9.0000")],
        },
        schema={**_SCHEMA, "quantity": pl.String},
    )
    synthesized = run_canonical_shape(_definition(), decimal_mismatch).failures
    synthesized_failure = next(f for f in synthesized if f.column == "quantity")

    # Native path: a non-Decimal dtype mismatch pandera reports itself
    # (sku_id is varchar -> String schema dtype; hand it an Int64 column).
    native_mismatch = pl.DataFrame(
        {
            "sku_id": [1],
            "event_subtype": ["SALE"],
            "quantity": [Decimal("2.000")],
            "unit_retail_price": [Decimal("10.0000")],
            "unit_sale_price": [Decimal("9.0000")],
        },
        schema={**_SCHEMA, "sku_id": pl.Int64},
    )
    native = run_canonical_shape(_definition(), native_mismatch).failures
    native_failure = next(f for f in native if f.column == "sku_id" and "dtype" in f.check)

    # Same type, same check-string shape, same column-level grain, same reason shape.
    assert type(synthesized_failure) is type(native_failure)
    assert synthesized_failure.check == "dtype('Decimal(precision=14, scale=3)')"
    assert native_failure.check == "dtype('String')"
    assert synthesized_failure.row_index is None and native_failure.row_index is None
    assert synthesized_failure.value == "String"  # the actual dtype, as native reports it
    assert native_failure.value == "Int64"
    template = "column {column!r} failed {check!r}"
    assert synthesized_failure.reason == template.format(column="quantity", check=synthesized_failure.check)
    assert native_failure.reason == template.format(column="sku_id", check=native_failure.check)


def test_null_in_required_column_fails() -> None:
    contribution = _contribution(sku_id=[None])
    result = run_canonical_shape(_definition(), contribution)
    assert not result.passed
    failure = next(f for f in result.failures if f.column == "sku_id" and f.check == "not_nullable")
    # Tenant-readable mandatory-field wording (the product rule in words).
    assert "required but arrived empty" in failure.reason


def test_missing_owned_column_fails() -> None:
    contribution = _contribution().drop("quantity")
    result = run_canonical_shape(_definition(), contribution)
    assert not result.passed
    assert any(
        f.check == "column_in_dataframe" and "missing owned column 'quantity'" in f.reason
        for f in result.failures
    )


def test_off_universe_column_in_contribution_fails_strict() -> None:
    # A column outside the source-owned set in the DATA fails loud (criterion 7's
    # no-columns-absent-from-live-schema chain: engine emits only mapping targets
    # -> this strict suite rejects anything else -> suite is drift-pinned).
    contribution = _contribution().with_columns(pl.lit("oops").alias("tenant_id"))
    result = run_canonical_shape(_definition(), contribution)
    assert not result.passed
    assert any(
        f.check == "column_in_schema" and "outside the source-owned" in f.reason for f in result.failures
    )


# -- Criterion 6, suite direction ---------------------------------------------------


def test_suite_column_set_equals_owned_set_both_directions() -> None:
    schema = materialize_canonical_shape(_definition())
    assert suite_column_set(schema) == frozenset(OWNED)


def test_owned_column_outside_mapping_produced_universe_is_drift() -> None:
    # trace_id is consumer-injected: a suite claiming a source owns it is drift.
    with pytest.raises(SuiteDriftError, match="cannot be source-owned"):
        materialize_canonical_shape(_definition(owned_columns=OWNED + ("trace_id",)))


def test_unknown_column_is_drift_via_provenance() -> None:
    with pytest.raises(SuiteDriftError):
        materialize_canonical_shape(_definition(owned_columns=OWNED + ("no_such_column",)))


def test_signal_history_suite_request_raises_by_design() -> None:
    with pytest.raises(SuiteDriftError, match="daily-compute output"):
        materialize_canonical_shape(
            CanonicalShapeSuiteDef(target_model=StoreSkuSignalHistory, owned_columns=("velocity_7day",))
        )


def test_empty_owned_set_fails_loud() -> None:
    with pytest.raises(SuiteDefinitionError, match="at least one column"):
        materialize_canonical_shape(_definition(owned_columns=()))


def test_column_checks_must_name_owned_columns() -> None:
    with pytest.raises(SuiteDefinitionError, match="non-owned column"):
        materialize_canonical_shape(_definition(column_checks={"currency": [Check.isin(["INR"])]}))


def test_owned_optional_decimal_materializes_its_numeric_dtype() -> None:
    # Regression (Slice 14d): an OWNED optional Decimal column (e.g. the hot
    # table's stock_qty: Numeric14_3 | None) must derive its numeric dtype. The
    # constraint lives in a FieldInfo nested in the Optional Annotated (pydantic
    # only decomposes it for REQUIRED fields); _resolve_annotation must flatten it.
    # Before the fix this raised SuiteDefinitionError("no max_digits/decimal_places").
    from dis_canonical import StoreSkuCurrentPosition

    schema = materialize_canonical_shape(
        CanonicalShapeSuiteDef(
            target_model=StoreSkuCurrentPosition,
            owned_columns=("sku_id", "product_name", "product_category", "stock_qty"),
        )
    )
    stock = schema.columns["stock_qty"]
    # pandera wraps the polars dtype; assert the derived precision/scale + nullability.
    assert "Decimal(precision=14, scale=3)" in repr(stock.dtype)
    assert stock.nullable is True  # the field is optional
