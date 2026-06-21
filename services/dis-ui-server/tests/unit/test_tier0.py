"""Tier-0 structural gate (D51/D52): structural-only, loud, reason-coded."""

from __future__ import annotations

import pytest

from dis_core.errors import UploadStructureError
from dis_ui_server.tier0 import MIN_CSV_RECORDS, Tier0Result, run_tier0

_IDS = {"tenant_id": "ten", "trace_id": "tr"}


def test_well_formed_csv_passes_with_data_row_count() -> None:
    result = run_tier0(b"sku,qty\nA-1,5\nB-2,3\n", **_IDS)
    assert result == Tier0Result(row_count=2)


def test_bom_is_tolerated() -> None:
    # Excel exports routinely lead with a BOM; the bytes are otherwise UTF-8.
    result = run_tier0(b"\xef\xbb\xbfsku,qty\nA-1,5\n", **_IDS)
    assert result.row_count == 1


@pytest.mark.parametrize("payload", [b"", b"   \n  \n"])
def test_empty_file_rejected(payload: bytes) -> None:
    with pytest.raises(UploadStructureError) as exc_info:
        run_tier0(payload, **_IDS)
    assert exc_info.value.reason == "empty_file"
    assert exc_info.value.tenant_id == "ten"  # context on every raise (rule 5)


def test_non_utf8_rejected() -> None:
    with pytest.raises(UploadStructureError) as exc_info:
        run_tier0(b"sku,qty\n\xff\xfe broken \x80\n", **_IDS)
    assert exc_info.value.reason == "not_utf8"


def test_pathological_csv_rejected() -> None:
    # The csv.Error branch: a field beyond the parser's field-size limit (the
    # module docstring is honest that decode + min-rows are the load-bearing
    # gates; this is the pathological-case backstop).
    monster = b"sku,blob\nA-1," + b"x" * 200_000 + b"\n"
    with pytest.raises(UploadStructureError) as exc_info:
        run_tier0(monster, **_IDS)
    assert exc_info.value.reason == "not_csv"


@pytest.mark.parametrize("payload", [b"sku,qty\n", b"sku,qty\n\n  ,  \n"])
def test_below_min_rows_rejected(payload: bytes) -> None:
    # A header with no data row carries nothing to ingest; blank/whitespace-only
    # records do not count toward the floor.
    with pytest.raises(UploadStructureError) as exc_info:
        run_tier0(payload, **_IDS)
    assert exc_info.value.reason == "below_min_rows"
    assert MIN_CSV_RECORDS == 2  # header + one data row (the documented floor)


def test_no_column_or_mapping_awareness() -> None:
    # Structural ONLY (D51): arbitrary headers and shapes pass; column checks
    # are tier 1 (the source-shape suite, downstream).
    assert run_tier0(b"anything,goes,here\n1,2,3\n", **_IDS).row_count == 1
