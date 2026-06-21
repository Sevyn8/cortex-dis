"""parse_chunk: parses raw CSV with the envelope-carried delimiter (Slice 16f).

The worker detects the delimiter in preflight and carries it on ingress.ready; the
consumer parses with it instead of a hardcoded comma. These pins cover the four
supported separators, the quoted-embedded-delimiter requirement (Polars' default
'"' quoting keeps a quoted separator as data, not a split), and the loud-empty /
loud-unparseable contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import polars as pl
import pytest

from dis_core.errors import EventContractError
from dis_core.ids import new_uuid7
from dis_storage import build_object_path
from dis_testing.fixtures import PRIMARY_STORE, PRIMARY_TENANT
from streaming_consumer.envelope import IngressReadyEvent
from streaming_consumer.pipeline import fetch as fetch_mod
from streaming_consumer.pipeline.fetch import BronzeMeta, fetch_chunk, parse_chunk

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_TENANT = "019e5e3c-b5d3-705f-9002-2451c4ca2626"
_TRACE = "019e9508-0000-7000-8000-000000000001"


def _parse(data: bytes, separator: str) -> pl.DataFrame:
    return parse_chunk(data, separator=separator, tenant_id=_TENANT, trace_id=_TRACE)


@pytest.mark.parametrize("sep", [",", ";", "\t", "|"])
def test_parses_with_each_delimiter_into_correct_columns(sep: str) -> None:
    data = f"sku{sep}qty{sep}price\nA-1{sep}5{sep}9.99\nB-2{sep}3{sep}4.50\n".encode()
    frame = _parse(data, sep)
    assert frame.columns == ["sku", "qty", "price"]
    assert frame.height == 2
    assert frame.row(0) == ("A-1", "5", "9.99")


def test_semicolon_file_does_not_become_one_mega_column() -> None:
    # The split-brain bug 16f fixes: a ';' file read with ';' yields real columns,
    # NOT a single column whose header is the ';'-joined string.
    data = b"sku;qty;price\nA-1;5;9.99\n"
    frame = _parse(data, ";")
    assert frame.width == 3
    assert frame.columns == ["sku", "qty", "price"]


def test_quoted_field_with_embedded_delimiter_stays_one_field() -> None:
    # A '"'-quoted value containing the separator is data, not a split (Polars
    # default quoting holds with an explicit separator — verified at plan time).
    data = b'name;note;qty\n"Acme; Inc";"a;b";5\nfoo;bar;6\n'
    frame = _parse(data, ";")
    assert frame.columns == ["name", "note", "qty"]
    assert frame.row(0) == ("Acme; Inc", "a;b", "5")
    assert frame.row(1) == ("foo", "bar", "6")


def test_comma_still_works_no_regression() -> None:
    frame = _parse(b"sku,qty\nA-1,5\n", ",")
    assert frame.columns == ["sku", "qty"]
    assert frame.row(0) == ("A-1", "5")


def test_empty_chunk_raises_loudly() -> None:
    with pytest.raises(EventContractError):
        _parse(b"sku;qty\n", ";")  # header only -> zero data rows


async def test_fetch_chunk_parses_with_the_event_delimiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Slice 16f WIRING: fetch_chunk must pass event.delimiter to parse_chunk, not a
    # hardcoded comma. read_bronze_row is faked (no DB); the parse is real, so a ';'
    # event + ';' bytes must yield real columns. Kills a fetch_chunk comma-hardcode.
    source_id = "sc_pos_v1"
    trace_id = new_uuid7()
    bronze_ref = new_uuid7()
    received = datetime(2026, 6, 5, tzinfo=UTC)
    bucket = "ithina-bronze-raw"
    key = build_object_path(
        tenant_id=PRIMARY_TENANT.uuid,
        source_id=source_id,
        trace_id=trace_id,
        event_ts=received,
        ext="csv",
    )
    gcs_uri = f"gs://{bucket}/{key}"
    event = IngressReadyEvent(
        schema_version=1,
        trace_id=trace_id,
        tenant_id=PRIMARY_TENANT.uuid,
        store_id=PRIMARY_STORE.uuid,
        source_id=source_id,
        template_id=new_uuid7(),
        bronze_ref=bronze_ref,
        gcs_uri=gcs_uri,
        received_ts=received,
        delimiter=";",
    )

    class _Store:
        def download_bytes(self, object_path: str) -> bytes:
            return b"sku;qty;price\nA-1;5;9.99\n"

    async def _fake_read_bronze_row(engine: AsyncEngine, ev: IngressReadyEvent) -> BronzeMeta:
        return BronzeMeta(
            bronze_id=bronze_ref,
            source_id=source_id,
            dis_channel="csv_upload",
            gcs_uri=gcs_uri,
            received_at=received,
            row_count=1,
        )

    monkeypatch.setattr(fetch_mod, "read_bronze_row", _fake_read_bronze_row)

    fetched = await fetch_chunk(cast("AsyncEngine", None), _Store(), event, bronze_bucket=bucket)
    assert fetched.frame.columns == ["sku", "qty", "price"]
    assert fetched.frame.width == 3
