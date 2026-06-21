"""Streaming multipart reader with a mid-stream byte ceiling (the Slice 8 pattern-setter).

The 10 MB cap is a security/integrity boundary the SERVER enforces by rejecting
as bytes cross the ceiling — never by reading the whole body and then checking
(a read-then-check lets an oversized POST exhaust memory first). Two guards:

1. ``Content-Length`` early-reject — cheap, but the header is caller-controlled
   and therefore spoofable; it is only the first check, never the boundary.
2. The REAL boundary: ``request.stream()`` chunks are counted and fed to
   python-multipart's push parser; the instant the raw body crosses the ceiling
   (or the file part alone crosses the file limit) ``PayloadTooLargeError``
   raises mid-stream and the remaining body is never read.

Deliberately NOT Starlette's ``request.form()``: that API buffers parts (spooling
to disk) before any size decision — the exact read-then-check this module exists
to avoid. This is the first file-body endpoint; later upload endpoints reuse this
module rather than re-deriving the guard.

Trust posture: unknown parts (e.g. a smuggled ``tenant_id`` field) are DRAINED
and ignored — they still count toward the ceiling but never reach the caller;
identity comes from the verified token only (the 14b foundation rule). Field
VALUES are never echoed into errors (a body can contain anything, including PII);
errors name the offending PART only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from python_multipart.multipart import MultipartParser, parse_options_header

from dis_core.errors import PayloadTooLargeError, UploadRequestError


class SupportsUploadStream(Protocol):
    """The slice of Starlette's ``Request`` this module consumes (test-fakeable)."""

    @property
    def headers(self) -> Mapping[str, str]: ...

    def stream(self) -> AsyncIterator[bytes]: ...


# A text field (template_id, store_code) has no business being large; past this
# it is a malformed request, not a big upload.
_MAX_TEXT_FIELD_BYTES = 1024


@dataclass(frozen=True)
class ParsedUpload:
    """The extracted upload: the file part's bytes + the decoded text fields."""

    file_bytes: bytes
    filename: str | None
    fields: dict[str, str]


class _MultipartCollector:
    """Push-parser callbacks collecting one file part + named text fields.

    Raises from inside the callbacks (they run synchronously under
    ``parser.write``), so a limit violation aborts the parse mid-chunk.
    """

    def __init__(self, *, file_part: str, text_parts: frozenset[str], max_file_bytes: int) -> None:
        self._file_part = file_part
        self._text_parts = text_parts
        self._max_file_bytes = max_file_bytes
        # Completed parts.
        self.file_data: bytearray | None = None
        self.filename: str | None = None
        self.fields: dict[str, str] = {}
        # In-flight part state.
        self._headers: dict[bytes, bytes] = {}
        self._header_field = bytearray()
        self._header_value = bytearray()
        self._part_name: str | None = None  # None => unknown part, drained
        self._part_is_file = False
        self._part_buffer = bytearray()

    # -- header accumulation ----------------------------------------------------

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        self._headers[bytes(self._header_field).lower()] = bytes(self._header_value)
        self._header_field.clear()
        self._header_value.clear()

    def on_headers_finished(self) -> None:
        disposition = self._headers.get(b"content-disposition")
        _, params = parse_options_header(disposition)
        raw_name = params.get(b"name")
        name = raw_name.decode("utf-8", errors="replace") if raw_name is not None else None
        if name == self._file_part:
            if self.file_data is not None:
                raise UploadRequestError(
                    "multipart body repeats the file part; exactly one is accepted",
                    part=self._file_part,
                )
            self._part_is_file = True
            self._part_name = name
            raw_filename = params.get(b"filename")
            self.filename = (
                raw_filename.decode("utf-8", errors="replace") if raw_filename is not None else None
            )
        elif name in self._text_parts:
            if name in self.fields:
                raise UploadRequestError(
                    f"multipart body repeats part {name!r}; exactly one is accepted",
                    part=name,
                )
            self._part_is_file = False
            self._part_name = name
        else:
            # Unknown part (e.g. a smuggled tenant_id): drained, never surfaced.
            self._part_is_file = False
            self._part_name = None

    # -- part data ----------------------------------------------------------------

    def on_part_begin(self) -> None:
        self._headers = {}
        self._part_buffer = bytearray()

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._part_name is None:
            return  # unknown part: drained (it still counted toward the raw ceiling)
        self._part_buffer += data[start:end]
        if self._part_is_file:
            if len(self._part_buffer) > self._max_file_bytes:
                raise PayloadTooLargeError(
                    "uploaded file crossed the size ceiling mid-stream",
                    limit_bytes=self._max_file_bytes,
                    observed_bytes=len(self._part_buffer),
                )
        elif len(self._part_buffer) > _MAX_TEXT_FIELD_BYTES:
            raise UploadRequestError(
                f"multipart part {self._part_name!r} exceeds the text-field size bound",
                part=self._part_name,
            )

    def on_part_end(self) -> None:
        if self._part_name is None:
            return
        if self._part_is_file:
            self.file_data = self._part_buffer
        else:
            try:
                self.fields[self._part_name] = bytes(self._part_buffer).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise UploadRequestError(
                    f"multipart part {self._part_name!r} is not valid UTF-8",
                    part=self._part_name,
                ) from exc
        self._part_name = None
        self._part_is_file = False


async def read_csv_upload(
    request: SupportsUploadStream,
    *,
    max_file_bytes: int,
    body_ceiling_bytes: int,
    file_part: str = "file",
    text_parts: frozenset[str] = frozenset({"template_id", "store_code"}),
) -> ParsedUpload:
    """Stream-parse the multipart upload under the ceiling; loud on every violation.

    Raises ``PayloadTooLargeError`` (413) the moment the declared or counted size
    crosses a limit, and ``UploadRequestError`` (400) for a body that is not
    multipart, a missing/repeated/oversized part, or an undecodable field.
    """
    content_type, params = parse_options_header(request.headers.get("content-type"))
    if content_type != b"multipart/form-data" or b"boundary" not in params:
        raise UploadRequestError(
            "request body must be multipart/form-data with a boundary",
            part=None,
        )

    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > body_ceiling_bytes:
        # The cheap first check. The header is spoofable, so the streaming guard
        # below remains the real boundary either way.
        raise PayloadTooLargeError(
            "declared Content-Length exceeds the upload ceiling",
            limit_bytes=body_ceiling_bytes,
            observed_bytes=int(declared),
        )

    collector = _MultipartCollector(file_part=file_part, text_parts=text_parts, max_file_bytes=max_file_bytes)
    parser = MultipartParser(
        params[b"boundary"],
        callbacks={
            "on_part_begin": collector.on_part_begin,
            "on_part_data": collector.on_part_data,
            "on_part_end": collector.on_part_end,
            "on_header_field": collector.on_header_field,
            "on_header_value": collector.on_header_value,
            "on_header_end": collector.on_header_end,
            "on_headers_finished": collector.on_headers_finished,
        },
    )

    total_raw = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total_raw += len(chunk)
        if total_raw > body_ceiling_bytes:
            # THE boundary: counted on the raw stream, before the parser sees the
            # chunk, so no arrangement of parts/framing can sidestep it.
            raise PayloadTooLargeError(
                "request body crossed the upload ceiling mid-stream",
                limit_bytes=body_ceiling_bytes,
                observed_bytes=total_raw,
            )
        parser.write(chunk)
    parser.finalize()

    if collector.file_data is None:
        raise UploadRequestError("multipart body carries no file part", part=file_part)
    missing = sorted(name for name in text_parts if not collector.fields.get(name, "").strip())
    if missing:
        raise UploadRequestError(
            f"multipart body is missing required part {missing[0]!r}",
            part=missing[0],
        )
    return ParsedUpload(
        file_bytes=bytes(collector.file_data),
        filename=collector.filename,
        fields=collector.fields,
    )
