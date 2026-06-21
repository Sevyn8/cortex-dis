"""Structural preflight: accepts well-formed CSV, fails loud + typed otherwise (AC3).

Includes the DuckDB pinned-behaviour CANARY (Slice 5 pattern): the specific DuckDB
behaviours the preflight relies on are asserted directly, so a version bump that
changes them fails here, not in production. No version string is asserted.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from csv_ingest_worker.preflight import PreflightResult, run_preflight
from dis_core.errors import DisError, PreflightFailedError

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "csvs"
_TENANT = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees
_TRACE = "019e8d88-4e76-7911-bb77-d8fcba1808a6"

_WELL_FORMED = (_FIXTURES / "well_formed.csv").read_bytes()
_NO_HEADER = (_FIXTURES / "no_header.csv").read_bytes()
_HEADER_ONLY = (_FIXTURES / "header_only.csv").read_bytes()
_BINARY = b"\x00\x01\x02\xff\xfe binary garbage \x00\x00\xff"


def _run(data: bytes) -> PreflightResult:
    return run_preflight(data, tenant_id=_TENANT, trace_id=_TRACE)


# ---------------------------------------------------------------------------
# Accepts a well-formed CSV.
# ---------------------------------------------------------------------------


def test_well_formed_csv_passes_with_structure() -> None:
    result = _run(_WELL_FORMED)
    assert result.columns == ("sku", "store_section", "qty_sold", "unit_price")
    assert result.row_count == 3
    assert result.size_bytes == len(_WELL_FORMED)
    assert len(result.column_types) == 4
    assert result.delimiter == ","  # the comma fixture sniffs as comma (Slice 16f)


# ---------------------------------------------------------------------------
# Delimiter detection (Slice 16f): the separator sniff_csv detects is captured
# and carried on PreflightResult. Comma, semicolon, tab, pipe.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sep", "expected"),
    [(",", ","), (";", ";"), ("\t", "\t"), ("|", "|")],
)
def test_delimiter_is_detected_and_carried(sep: str, expected: str) -> None:
    # Same 3-column structure under each separator -> the detected delimiter is
    # the separator, and the columns still split correctly (structure intact).
    data = f"sku{sep}qty{sep}price\nA-1{sep}5{sep}9.99\nB-2{sep}3{sep}4.50\n".encode()
    result = _run(data)
    assert result.delimiter == expected
    assert result.columns == ("sku", "qty", "price")
    assert result.row_count == 2


def test_quoted_fields_and_crlf_pass() -> None:
    data = b'sku,note\r\n"A-1","has, a comma"\r\n"B-2","plain"\r\n'
    result = _run(data)
    assert result.columns == ("sku", "note")
    assert result.row_count == 2


# ---------------------------------------------------------------------------
# Loud, typed failures (never silent, never a skip).
# ---------------------------------------------------------------------------


def test_binary_garbage_fails_loud_typed() -> None:
    with pytest.raises(PreflightFailedError) as exc_info:
        _run(_BINARY)
    err = exc_info.value
    assert err.reason == "not_csv"
    assert err.tenant_id == _TENANT
    assert err.trace_id == _TRACE
    assert issubclass(PreflightFailedError, DisError)


def test_headerless_fails_structural_sniff() -> None:
    with pytest.raises(PreflightFailedError) as exc_info:
        _run(_NO_HEADER)
    assert exc_info.value.reason == "no_header"


def test_header_only_zero_rows_fails() -> None:
    with pytest.raises(PreflightFailedError) as exc_info:
        _run(_HEADER_ONLY)
    assert exc_info.value.reason == "no_data_rows"


def test_empty_object_fails() -> None:
    with pytest.raises(PreflightFailedError) as exc_info:
        _run(b"")
    assert exc_info.value.reason == "not_csv"


def test_failure_detail_never_carries_file_content() -> None:
    # DuckDB messages can quote cell values; the typed error must carry only the
    # stable reason + exception class name (hard rule 2: no payload in errors/logs).
    secret = b"sku,email\nSECRET-CELL-VALUE,broken\x00\xff\x00"
    with pytest.raises(PreflightFailedError) as exc_info:
        _run(secret)
    err = exc_info.value
    blob = " ".join(str(part) for part in (err.message, err.reason, err.detail))
    assert "SECRET-CELL-VALUE" not in blob


# ---------------------------------------------------------------------------
# CANARY: the DuckDB behaviours the preflight relies on (Slice 5 pattern).
# A version bump that changes any of these must fail HERE.
# ---------------------------------------------------------------------------


def test_canary_sniff_csv_binds_prepared_param_and_returns_columns_shape(
    tmp_path: Path,
) -> None:
    # Relied-on: `sniff_csv(?)` accepts a prepared parameter, and Columns comes back
    # as a Python list of {'name': ..., 'type': ...} dicts.
    path = tmp_path / "canary.csv"
    path.write_bytes(_WELL_FORMED)
    con = duckdb.connect()
    try:
        row = con.execute("SELECT Delimiter, HasHeader, Columns FROM sniff_csv(?)", [str(path)]).fetchone()
    finally:
        con.close()
    assert row is not None
    delimiter, has_header, columns = row
    # Relied-on (Slice 16f): Delimiter comes back as a single-character string.
    assert isinstance(delimiter, str) and len(delimiter) == 1
    assert delimiter == ","
    assert has_header is True
    assert isinstance(columns, list)
    assert {"name", "type"} <= set(columns[0].keys())
    assert [c["name"] for c in columns] == ["sku", "store_section", "qty_sold", "unit_price"]


def test_canary_sniff_csv_detects_semicolon_delimiter(tmp_path: Path) -> None:
    # Relied-on (Slice 16f): a non-comma file is sniffed with its real delimiter,
    # exposed in the Delimiter column as a single char.
    path = tmp_path / "canary_semi.csv"
    path.write_bytes(b"sku;qty;price\nA-1;5;9.99\n")
    con = duckdb.connect()
    try:
        row = con.execute("SELECT Delimiter FROM sniff_csv(?)", [str(path)]).fetchone()
    finally:
        con.close()
    assert row is not None and row[0] == ";"


def test_canary_sniffer_detects_headerless_numeric_file(tmp_path: Path) -> None:
    # Relied-on: an all-numeric first row is sniffed as HasHeader=False.
    path = tmp_path / "canary_nohdr.csv"
    path.write_bytes(_NO_HEADER)
    con = duckdb.connect()
    try:
        row = con.execute("SELECT HasHeader FROM sniff_csv(?)", [str(path)]).fetchone()
    finally:
        con.close()
    assert row is not None and row[0] is False


def test_canary_type_sniff_infers_expected_types(tmp_path: Path) -> None:
    # Relied-on: the sniffer's type inference yields VARCHAR/BIGINT/DOUBLE for a
    # string/int/decimal fixture (the type sniff the preflight reports).
    path = tmp_path / "canary_types.csv"
    path.write_bytes(b"name,qty,price\nx,5,9.99\ny,3,4.50\n")
    con = duckdb.connect()
    try:
        row = con.execute("SELECT Columns FROM sniff_csv(?)", [str(path)]).fetchone()
    finally:
        con.close()
    assert row is not None
    types = [c["type"] for c in row[0]]
    assert types == ["VARCHAR", "BIGINT", "DOUBLE"]


def test_canary_empty_file_does_not_raise_it_fabricates_a_headerless_column(
    tmp_path: Path,
) -> None:
    # Relied-on BOUNDARY: DuckDB does NOT raise on an empty file — it sniffs
    # HasHeader=False with a fabricated 'column0'. run_preflight guards empty input
    # itself because of this; if a future DuckDB starts raising, the guard ordering
    # assumption changes and this canary flags it.
    path = tmp_path / "canary_empty.csv"
    path.write_bytes(b"")
    con = duckdb.connect()
    try:
        row = con.execute("SELECT HasHeader, Columns FROM sniff_csv(?)", [str(path)]).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row[0] is False
    assert [c["name"] for c in row[1]] == ["column0"]


def test_canary_unparseable_input_raises_duckdb_error(tmp_path: Path) -> None:
    # Relied-on: unparseable input raises a duckdb.Error subclass (what run_preflight
    # catches); if a future DuckDB returns junk instead of raising, this fails.
    path = tmp_path / "canary.bin"
    path.write_bytes(_BINARY)
    con = duckdb.connect()
    try:
        with pytest.raises(duckdb.Error):
            con.execute("SELECT * FROM sniff_csv(?)", [str(path)]).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Scope: the preflight performs no column- or mapping-aware checks (AC3).
# A test cannot prove a feature's absence; the expressible part is the import scope.
# ---------------------------------------------------------------------------


def test_preflight_module_imports_no_mapping_or_validation_lib() -> None:
    import csv_ingest_worker.preflight as preflight_module

    source = Path(str(preflight_module.__file__)).read_text()
    for forbidden in ("dis_mapping", "dis_validation", "pandera"):
        assert forbidden not in source, f"preflight must stay structural; found {forbidden!r}"
