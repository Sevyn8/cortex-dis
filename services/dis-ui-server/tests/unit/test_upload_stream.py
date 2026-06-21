"""The streaming multipart reader: the mid-stream ceiling is the REAL boundary.

The load-bearing assertions here are the ones the slice contract names: an
oversized body is rejected BEFORE it is fully read (proven with a counting
stream), the Content-Length early-reject fires without reading any body, and a
smuggled unknown part (e.g. ``tenant_id``) is drained and ignored.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

import pytest

from dis_core.errors import PayloadTooLargeError, UploadRequestError
from dis_ui_server.upload_stream import read_csv_upload

_BOUNDARY = "testboundary42"


class _FakeRequest:
    """Duck-typed Starlette request: ``headers.get`` + ``stream()``, counted."""

    def __init__(
        self,
        chunks: Iterable[bytes],
        *,
        content_type: str = f"multipart/form-data; boundary={_BOUNDARY}",
        content_length: int | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self.chunks_served = 0
        headers = {"content-type": content_type}
        if content_length is not None:
            headers["content-length"] = str(content_length)
        self.headers = headers

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.chunks_served += 1
            yield chunk


def _part(name: str, payload: bytes, *, filename: str | None = None) -> bytes:
    disposition = f'Content-Disposition: form-data; name="{name}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    return f"--{_BOUNDARY}\r\n{disposition}\r\n\r\n".encode() + payload + b"\r\n"


def _body(*parts: bytes) -> bytes:
    return b"".join(parts) + f"--{_BOUNDARY}--\r\n".encode()


def _good_body(file_payload: bytes = b"sku,qty\nA-1,5\n") -> bytes:
    return _body(
        _part("template_id", b"019e98c9-df80-7649-98cd-83fb6293777a"),
        _part("store_code", b"TX-101"),
        _part("file", file_payload, filename="sales.csv"),
    )


def _chunked(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


async def _read(request: _FakeRequest, *, max_file: int = 1024, ceiling: int = 4096) -> object:
    return await read_csv_upload(request, max_file_bytes=max_file, body_ceiling_bytes=ceiling)


# ---------------------------------------------------------------------------
# Happy path + the trust posture.
# ---------------------------------------------------------------------------


async def test_well_formed_upload_parses() -> None:
    request = _FakeRequest(_chunked(_good_body(), 128))
    parsed = await read_csv_upload(request, max_file_bytes=1024, body_ceiling_bytes=4096)
    assert parsed.file_bytes == b"sku,qty\nA-1,5\n"
    assert parsed.filename == "sales.csv"
    assert parsed.fields == {
        "template_id": "019e98c9-df80-7649-98cd-83fb6293777a",
        "store_code": "TX-101",
    }


async def test_smuggled_unknown_part_is_drained_and_ignored() -> None:
    # The foundation rule, at the parser layer: a body tenant_id never surfaces.
    body = _body(
        _part("tenant_id", b"019e5e3c-b5d6-7eed-93f9-3778a7a7a160"),  # smuggled
        _part("template_id", b"019e98c9-df80-7649-98cd-83fb6293777a"),
        _part("store_code", b"TX-101"),
        _part("file", b"sku,qty\nA-1,5\n"),
    )
    parsed = await _read(_FakeRequest([body]))
    assert "tenant_id" not in parsed.fields  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The size boundary: early check + the real mid-stream guard.
# ---------------------------------------------------------------------------


async def test_declared_content_length_rejects_before_any_body_read() -> None:
    request = _FakeRequest(_chunked(_good_body(), 64), content_length=10_000)
    with pytest.raises(PayloadTooLargeError) as exc_info:
        await _read(request, ceiling=4096)
    assert request.chunks_served == 0  # the stream was never touched
    assert exc_info.value.observed_bytes == 10_000
    assert exc_info.value.limit_bytes == 4096


async def test_body_crossing_ceiling_aborts_mid_stream_not_after_full_read() -> None:
    # A spoofed-small Content-Length sails past the early check; the streaming
    # guard is the real boundary. The counting stream proves the abort happened
    # within one chunk of the ceiling — the body was NOT fully read.
    chunk_size = 1024
    oversized = _good_body(file_payload=b"x" * 100_000)  # ~25x the ceiling
    chunks = _chunked(oversized, chunk_size)
    request = _FakeRequest(chunks, content_length=10)  # spoofed: declared tiny
    with pytest.raises(PayloadTooLargeError):
        await _read(request, max_file=1_000_000, ceiling=4096)
    assert request.chunks_served < len(chunks)  # never drained
    assert request.chunks_served * chunk_size <= 4096 + chunk_size  # within one chunk


async def test_file_part_crossing_its_own_limit_aborts_mid_stream() -> None:
    # The file limit binds tighter than the raw ceiling (framing allowance).
    chunk_size = 512
    body = _good_body(file_payload=b"x" * 8_000)
    chunks = _chunked(body, chunk_size)
    request = _FakeRequest(chunks)
    with pytest.raises(PayloadTooLargeError) as exc_info:
        await _read(request, max_file=2048, ceiling=1_000_000)
    assert exc_info.value.limit_bytes == 2048
    assert request.chunks_served < len(chunks)


# ---------------------------------------------------------------------------
# Malformed requests: 400-mapped, part-named, values never echoed.
# ---------------------------------------------------------------------------


async def test_non_multipart_content_type_rejected() -> None:
    request = _FakeRequest([b"{}"], content_type="application/json")
    with pytest.raises(UploadRequestError):
        await _read(request)


@pytest.mark.parametrize("missing", ["file", "template_id", "store_code"])
async def test_missing_required_part_rejected_by_name(missing: str) -> None:
    parts = {
        "template_id": _part("template_id", b"019e98c9-df80-7649-98cd-83fb6293777a"),
        "store_code": _part("store_code", b"TX-101"),
        "file": _part("file", b"sku,qty\nA-1,5\n"),
    }
    del parts[missing]
    request = _FakeRequest([_body(*parts.values())])
    with pytest.raises(UploadRequestError) as exc_info:
        await _read(request)
    assert exc_info.value.part == missing


async def test_repeated_file_part_rejected() -> None:
    body = _body(
        _part("template_id", b"019e98c9-df80-7649-98cd-83fb6293777a"),
        _part("store_code", b"TX-101"),
        _part("file", b"a,b\n1,2\n"),
        _part("file", b"c,d\n3,4\n"),
    )
    with pytest.raises(UploadRequestError) as exc_info:
        await _read(_FakeRequest([body]))
    assert exc_info.value.part == "file"


async def test_oversized_text_field_rejected_as_request_error() -> None:
    body = _body(
        _part("template_id", b"x" * 5_000),  # a text field has no business this large
        _part("store_code", b"TX-101"),
        _part("file", b"a,b\n1,2\n"),
    )
    with pytest.raises(UploadRequestError) as exc_info:
        await _read(_FakeRequest([body]), ceiling=100_000)
    assert exc_info.value.part == "template_id"


async def test_error_never_echoes_field_values() -> None:
    secret = b"PII-LADEN-VALUE-MUST-NOT-ECHO"
    body = _body(
        _part("template_id", secret + b"-" + b"x" * 5_000),
        _part("store_code", b"TX-101"),
        _part("file", b"a,b\n1,2\n"),
    )
    with pytest.raises(UploadRequestError) as exc_info:
        await _read(_FakeRequest([body]), ceiling=100_000)
    assert secret.decode() not in str(exc_info.value)
