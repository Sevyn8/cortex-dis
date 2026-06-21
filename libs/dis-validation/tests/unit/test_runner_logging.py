"""Logging discipline (criterion 7): bound context, never a cell value.

The failure OBJECTS may carry offending values (the quarantine payload); log
lines carry check/column names and counts only.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import polars as pl
import pytest

from dis_canonical import StoreSkuSaleEvent
from dis_core.logging import LogContext
from dis_validation import (
    CanonicalShapeSuiteDef,
    ColumnExpectation,
    SourceShapeSuiteDef,
    run_canonical_shape,
    run_source_shape,
)

SECRET = "SECRET-CELL-VALUE-77"


def test_source_shape_logs_bind_context_and_exclude_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    definition = SourceShapeSuiteDef(
        expected_columns=(ColumnExpectation(name="item_code", pattern=r"^SKU-\d+$"),)
    )
    chunk = pl.DataFrame({"item_code": [SECRET]})
    with caplog.at_level(logging.DEBUG, logger="dis-validation"):
        result = run_source_shape(
            definition, chunk, log_context=LogContext(tenant_id="ten-1", trace_id="tr-1")
        )

    assert not result.passed
    assert result.failures[0].value == SECRET  # the quarantine payload keeps it...
    assert caplog.records, "expected a failure log line"
    for record in caplog.records:  # ...but no log line ever carries it
        assert SECRET not in record.getMessage()
        assert SECRET not in str(record.__dict__)
        assert record.__dict__.get("tenant_id") == "ten-1"
        assert record.__dict__.get("trace_id") == "tr-1"
        assert record.__dict__.get("service") == "dis-validation"
        assert record.__dict__.get("stage") == "validate.source_shape"


def test_canonical_shape_logs_bind_context_and_exclude_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    definition = CanonicalShapeSuiteDef(target_model=StoreSkuSaleEvent, owned_columns=("sku_id", "quantity"))
    contribution = pl.DataFrame(
        {"sku_id": [SECRET + ("x" * 129)], "quantity": [Decimal("1.000")]},
        schema={"sku_id": pl.String, "quantity": pl.Decimal(14, 3)},
    )
    with caplog.at_level(logging.DEBUG, logger="dis-validation"):
        result = run_canonical_shape(
            definition, contribution, log_context=LogContext(tenant_id="ten-1", trace_id="tr-1")
        )

    assert not result.passed
    assert caplog.records, "expected a failure log line"
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in str(record.__dict__)
        assert record.__dict__.get("stage") == "validate.canonical_shape"
