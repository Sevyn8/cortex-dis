"""The 10 MB guard through the REAL ASGI stack — not the hand-rolled stream fake.

``test_upload_stream.py`` proves the reader aborts mid-stream when fed a
synthetic counting stream. This module closes the remaining gap: that the SAME
property holds when the bytes arrive through the actual application — CORS
middleware, exception middleware, FastAPI routing, the auth dependency, and
Starlette's ``request.stream()`` pulling from the ASGI ``receive`` callable.

The proof seam is ``receive`` itself: the server boundary. The app is invoked
directly (its real ``__call__``, lifespan run via the router's lifespan context)
with a counting ``receive`` that serves an oversized multipart body in chunks.
If ANYTHING in the stack buffered the body before the handler's reader (e.g. a
middleware or FastAPI pre-reading), every chunk would be consumed before the
413; the assertion that consumption stopped within one chunk of the ceiling is
therefore a whole-stack property, not a unit property.

No Content-Length is declared (the spoof posture: the early check has nothing
to reject, so the STREAMING guard must be the boundary).
"""

from __future__ import annotations

import json
import time
from collections.abc import MutableMapping
from typing import Any

import jwt
import pytest

from dis_ui_server.config import CSV_UPLOAD_BODY_CEILING_BYTES, CSV_UPLOAD_MAX_FILE_BYTES
from dis_ui_server.main import create_app

_BOUNDARY = "asgi-boundary-7f"
_CHUNK = 256 * 1024  # 256 KiB per ASGI http.request message


def _token() -> str:
    # The dev-stub contract (auth/verifier.py constants), minted inline so this
    # module needs no fixture plumbing beyond the env.
    from dis_ui_server.auth.verifier import (
        DEV_STUB_ALGORITHM,
        DEV_STUB_AUDIENCE,
        DEV_STUB_ISSUER,
        DEV_STUB_SECRET,
    )

    now = int(time.time())
    payload = {
        "sub": "user-asgi",
        "iss": DEV_STUB_ISSUER,
        "aud": DEV_STUB_AUDIENCE,
        "iat": now,
        "exp": now + 600,
        "tenant_id": "019e5e3c-b5d3-705f-9002-2451c4ca2626",
        "user_type": "TENANT",
    }
    return jwt.encode(payload, DEV_STUB_SECRET, algorithm=DEV_STUB_ALGORITHM)


def _oversized_multipart_body() -> bytes:
    """A well-formed multipart body whose file part is ~2x the file ceiling."""
    file_payload = b"x" * (2 * CSV_UPLOAD_MAX_FILE_BYTES)
    parts = b""
    for name, value in (
        ("template_id", b"019e98c9-df80-7649-98cd-83fb6293777a"),
        ("store_code", b"TX-101"),
    ):
        parts += (
            f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value
            + b"\r\n"
        )
    parts += (
        f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="big.csv"\r\n\r\n'.encode()
        + file_payload
        + b"\r\n"
    )
    return parts + f"--{_BOUNDARY}--\r\n".encode()


class _CountingReceive:
    """Serves the body in chunks; counts how many the app actually pulled."""

    def __init__(self, body: bytes, chunk_size: int) -> None:
        self._chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]
        self.total_chunks = len(self._chunks)
        self.served = 0

    async def __call__(self) -> dict[str, Any]:
        if self.served < self.total_chunks:
            chunk = self._chunks[self.served]
            self.served += 1
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": self.served < self.total_chunks,
            }
        return {"type": "http.request", "body": b"", "more_body": False}


async def test_oversized_post_through_the_real_app_aborts_mid_stream(
    unit_env: None,
) -> None:
    app = create_app()
    body = _oversized_multipart_body()
    receive = _CountingReceive(body, _CHUNK)
    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/csv-uploads",
        "raw_path": b"/api/v1/csv-uploads",
        "root_path": "",
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {_token()}".encode()),
            (b"content-type", f"multipart/form-data; boundary={_BOUNDARY}".encode()),
            # Deliberately NO content-length: the spoof posture — the early
            # check has nothing to reject, the streaming guard must hold alone.
        ],
        "state": {},
    }

    # The REAL app: lifespan (engine/storage/publisher/audit wiring), CORS +
    # exception middleware, routing, the auth dependency, the handler.
    async with app.router.lifespan_context(app):
        await app(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413

    body_message = next(m for m in sent if m["type"] == "http.response.body")
    envelope = json.loads(bytes(body_message["body"]))
    assert envelope["error"]["code"] == "payload_too_large"
    assert envelope["error"]["trace_id"] is not None  # minted + bound before the read

    # THE whole-stack property: the body was NOT buffered anywhere before the
    # guard. Consumption stopped within one chunk of the ceiling — had any layer
    # (middleware, FastAPI, Starlette) read the body first, every chunk would
    # have been served before the 413.
    ceiling_chunks = CSV_UPLOAD_BODY_CEILING_BYTES // _CHUNK + 1
    assert receive.served < receive.total_chunks, "the FULL body was read — read-then-check!"
    assert receive.served <= ceiling_chunks + 1, (
        f"consumed {receive.served} chunks; the guard should stop within one chunk "
        f"of the ceiling (~{ceiling_chunks})"
    )


async def test_undersized_post_through_the_real_app_reads_to_completion(
    unit_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The control: a small VALID body is consumed fully and proceeds past the
    # guard (failing later only on tier-0/resolution, not on size) — proving the
    # counting harness itself isn't what stops the read.
    app = create_app()
    small = _oversized_multipart_body().replace(b"x" * (2 * CSV_UPLOAD_MAX_FILE_BYTES), b"sku,qty\nA,1\n")
    receive = _CountingReceive(small, 1024)
    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    async def no_template(*args: Any, **kwargs: Any) -> Any:
        from dis_core.errors import ResourceNotFoundError

        raise ResourceNotFoundError("nope", resource="mapping_template", identifier="x")

    monkeypatch.setattr("dis_ui_server.handlers.csv_uploads.resolve_active_template", no_template)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/csv-uploads",
        "raw_path": b"/api/v1/csv-uploads",
        "root_path": "",
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {_token()}".encode()),
            (b"content-type", f"multipart/form-data; boundary={_BOUNDARY}".encode()),
        ],
        "state": {},
    }
    async with app.router.lifespan_context(app):
        await app(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 404  # past the size guard, past tier-0, into resolution
    assert receive.served == receive.total_chunks  # the small body WAS read fully
