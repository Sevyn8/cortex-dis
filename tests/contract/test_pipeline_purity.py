"""No-I/O contract for the pure pipeline libs (slice-05 criterion 1).

The import-linter contracts (root pyproject ``[tool.importlinter]``) hold the
import graph; this test holds the RUNTIME claim: a full engine pass plus both
validation suites execute end-to-end while ``socket.socket``,
``socket.create_connection``, and ``builtins.open`` are tripwired to raise. Any
runtime file or network touch surfaces the guard exception and the test goes red.

Honesty bound (stated, not over-claimed): one execution path cannot prove the
UNIVERSAL absence of I/O — that residue is held by review. The D4 runner-swap
guarantee rests on this purity, so additions of I/O to these libs must fail here.

These are contract tests over pure libs: no stack, no fixtures, no DB — they run
under a bare ``uv run pytest``.
"""

from __future__ import annotations

import builtins
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, NoReturn

import polars as pl
import pytest
from pandera import Check

from dis_canonical import StoreSkuSaleEvent
from dis_enrichment import CURRENT_POSITION, apply_enrichment
from dis_mapping import SourceMapping, apply_mapping
from dis_validation import (
    CanonicalShapeSuiteDef,
    SourceShapeSuiteDef,
    run_canonical_shape,
    run_source_shape,
)


class IoAttemptError(AssertionError):
    """Raised by the tripwires: the pure path attempted I/O."""


@contextmanager
def _no_io() -> Iterator[None]:
    """Tripwire sockets and file opens for the duration of the block."""

    def _blocked(*args: Any, **kwargs: Any) -> NoReturn:
        raise IoAttemptError("runtime I/O attempted inside a pure pipeline lib")

    original_socket = socket.socket
    original_create_connection = socket.create_connection
    original_open = builtins.open
    socket.socket = _blocked  # type: ignore[misc,assignment]
    socket.create_connection = _blocked
    builtins.open = _blocked
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[misc]
        socket.create_connection = original_create_connection
        builtins.open = original_open


def test_tripwires_actually_trip() -> None:
    # The guard itself must be live, or a green run proves nothing.
    with _no_io():
        with pytest.raises(IoAttemptError):
            socket.socket()
        with pytest.raises(IoAttemptError):
            open("/dev/null")  # noqa: PTH123, SIM115


def test_full_engine_and_both_suites_run_without_any_io() -> None:
    mapping = SourceMapping.model_validate(
        {
            "version": 1,
            "rename": {
                "itemcd": "sku_id",
                "subtype": "event_subtype",
                "qty": "quantity",
                "rrp": "unit_retail_price",
                "price": "unit_sale_price",
                "sold_at": "source_sale_timestamp",
            },
            "normalize": {
                "sku_id": [
                    {"op": "normalize_whitespace", "args": {}},
                    {"op": "normalize_case", "args": {"mode": "upper"}},
                ],
                "quantity": [
                    {
                        "op": "parse_decimal",
                        "args": {"decimal_separator": ".", "thousands_separator": None},
                    }
                ],
                "unit_retail_price": [
                    {
                        "op": "parse_decimal",
                        "args": {"decimal_separator": ",", "thousands_separator": "."},
                    }
                ],
                "unit_sale_price": [
                    {
                        "op": "parse_decimal",
                        "args": {"decimal_separator": ",", "thousands_separator": "."},
                    }
                ],
                "source_sale_timestamp": [
                    {
                        "op": "parse_datetime",
                        "args": {"format": "%d-%m-%Y %H:%M", "timezone": "Asia/Kolkata"},
                    }
                ],
            },
            "cast": {
                "quantity": {"type": "decimal", "precision": 14, "scale": 3},
                "unit_retail_price": {"type": "decimal", "precision": 12, "scale": 4},
                "unit_sale_price": {"type": "decimal", "precision": 12, "scale": 4},
                "source_sale_timestamp": {"type": "datetime"},
            },
            "derive": {
                "event_date": [
                    {"op": "date_from_datetime", "args": {"source_column": "source_sale_timestamp"}}
                ]
            },
        }
    )
    chunk = pl.DataFrame(
        {
            "itemcd": ["  ab-1 ", "cd-2", "ef-3"],
            "subtype": ["SALE", "RETURN", "SALE"],
            "qty": ["1.5", "2", "bogus"],  # one per-cell failure exercises that path too
            "rrp": ["1.299,50", "10,00", "5,00"],
            "price": ["999,00", "9,00", "4,50"],
            "sold_at": ["31-12-2025 23:30", "01-01-2026 10:00", "02-01-2026 11:00"],
        }
    )
    source_def = SourceShapeSuiteDef.from_rename(mapping.rename)
    canonical_def = CanonicalShapeSuiteDef(
        target_model=StoreSkuSaleEvent,
        owned_columns=(
            "sku_id",
            "event_subtype",
            "quantity",
            "unit_retail_price",
            "unit_sale_price",
            "source_sale_timestamp",
            "event_date",
        ),
        frame_checks=[
            Check(
                lambda data: data.lazyframe.select(pl.col("unit_sale_price") <= pl.col("unit_retail_price")),
                name="unit_sale_price<=unit_retail_price",
            )
        ],
    )

    with _no_io():
        source_result = run_source_shape(source_def, chunk)
        assert source_result.passed

        mapping_result = apply_mapping(mapping, chunk)
        assert len(mapping_result.failures) == 1  # the 'bogus' quantity cell
        assert mapping_result.contribution.height == 2

        canonical_result = run_canonical_shape(canonical_def, mapping_result.contribution)
        assert canonical_result.passed
        assert mapping_result.contribution["quantity"].to_list() == [
            Decimal("1.500"),
            Decimal("2.000"),
        ]


def test_enrichment_runs_without_any_io() -> None:
    # slice-5b: the enrichment engine is pure too (no DB read — the consumer hands in
    # the facts). A full apply runs under the tripwire.
    contribution = pl.DataFrame({"sku_id": ["A", "B"], "currency": ["USD", "USD"]})
    with _no_io():
        result = apply_enrichment(
            contribution,
            {"currency": "INR", "tax_treatment": "INCLUSIVE"},
            table=CURRENT_POSITION,
        )
    assert result.contribution["currency"].to_list() == ["INR", "INR"]
    assert result.contribution["tax_treatment"].to_list() == ["INCLUSIVE", "INCLUSIVE"]
