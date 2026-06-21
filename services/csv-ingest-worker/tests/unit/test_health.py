"""Readiness healthz (slice 40a): 200 fresh / 503 stale / 404 elsewhere.

Drives the ASGI callable directly (scope dict + captured ``send``) — no socket,
no uvicorn; the staleness branch is driven by pushing ``last_beat`` back.
"""

from __future__ import annotations

import time
from typing import Any

from csv_ingest_worker.health import HEALTH_PATH, Heartbeat, make_healthz_app

_THRESHOLD = 60.0


async def _call(app: Any, path: str) -> tuple[int, bytes]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:  # pragma: no cover - never pulled
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(dict(message))

    await app({"type": "http", "method": "GET", "path": path}, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


async def test_fresh_heartbeat_is_200_ok() -> None:
    heartbeat = Heartbeat()  # stamped at construction: startup counts as fresh
    status, body = await _call(make_healthz_app(heartbeat, _THRESHOLD), HEALTH_PATH)
    assert status == 200
    assert body == b'{"status":"ok"}'


async def test_stale_heartbeat_is_503() -> None:
    heartbeat = Heartbeat()
    heartbeat.last_beat = time.monotonic() - (_THRESHOLD + 1)  # past the threshold
    status, body = await _call(make_healthz_app(heartbeat, _THRESHOLD), HEALTH_PATH)
    assert status == 503
    assert body == b'{"status":"stale"}'


async def test_beat_refreshes_a_stale_heartbeat() -> None:
    heartbeat = Heartbeat()
    heartbeat.last_beat = time.monotonic() - (_THRESHOLD + 1)
    heartbeat.beat()
    status, _ = await _call(make_healthz_app(heartbeat, _THRESHOLD), HEALTH_PATH)
    assert status == 200


async def test_other_path_is_404() -> None:
    status, _ = await _call(make_healthz_app(Heartbeat(), _THRESHOLD), "/other")
    assert status == 404
