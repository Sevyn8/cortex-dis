"""CANARY for the D50 upstream bug — the removal trigger for the workaround.

pandera 0.31.1's polars engine raises a raw ``AssertionError`` ("The return is
expected to be of Decimal class", ``pandera/engines/polars_engine.py``) when a
schema column declaring ``pl.Decimal`` meets non-Decimal data, instead of
reporting a dtype failure case. ``dis_validation.runner._decimal_dtype_precheck``
works around exactly that case.

This test feeds pandera the broken case DIRECTLY (no dis-validation machinery)
and asserts the BUG STILL EXISTS. The moment any upstream behaviour change —
within or beyond the version pin — makes pandera stop raising this exact raw
AssertionError, this test goes RED, forcing the workaround's removal review. Do
NOT "fix" this test by loosening it; delete the workaround instead (D50).
"""

from __future__ import annotations

import pandera.errors
import pandera.polars as pa
import polars as pl
import pytest


def _decimal_schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema({"q": pa.Column(pl.Decimal(14, 3), nullable=False)})


def test_pandera_decimal_vs_string_still_raises_raw_assertion_error() -> None:
    with pytest.raises(AssertionError, match="expected to be of Decimal class"):
        _decimal_schema().validate(pl.DataFrame({"q": ["2.000"]}), lazy=True)


def test_pandera_decimal_vs_float_still_raises_raw_assertion_error() -> None:
    with pytest.raises(AssertionError, match="expected to be of Decimal class"):
        _decimal_schema().validate(pl.DataFrame({"q": [2.0]}), lazy=True)


def test_pandera_decimal_vs_decimal_mismatch_reports_natively() -> None:
    # The boundary of the bug (and of the workaround's scope): Decimal-vs-Decimal
    # precision/scale mismatch is NOT broken — pandera reports it natively. If
    # this starts crashing too, the workaround's scope is wrong, not just stale.
    frame = pl.DataFrame({"q": [1]}).with_columns(pl.col("q").cast(pl.Decimal(12, 4)))
    with pytest.raises(pandera.errors.SchemaErrors) as exc_info:
        _decimal_schema().validate(frame, lazy=True)
    cases = exc_info.value.failure_cases
    assert cases.height == 1
    assert cases["check"][0] == "dtype('Decimal(precision=14, scale=3)')"
