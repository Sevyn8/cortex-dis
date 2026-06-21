"""Readiness healthz for the pull-loop consumer (slice 40a, Cloud Run Service mode).

``Heartbeat`` is the shared liveness marker: the pull loop beats once per cycle
UNCONDITIONALLY (all modes — only the SERVER is toggled, so the loop never
branches on environment). ``make_healthz_app`` is a raw ASGI callable (no FastAPI
dependency in the consumer): ``GET /healthz`` answers 200 while the heartbeat is
fresh and 503 once it goes stale — READINESS, not liveness, so a dead loop behind
a living HTTP server reports unhealthy and Cloud Run restarts the consumer (the
zombie-worker close). Served by ``uvicorn.Server(...).serve()`` as a sibling
async task of the loop, under the one event loop (see ``main.py``).

The clock is ``time.monotonic()`` (wall-clock-safe); the heartbeat is stamped at
construction so startup is not an instant 503 before the first cycle.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

# Minimal structural ASGI types (the uvicorn boundary; no framework dependency).
Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

HEALTH_PATH = "/healthz"

_OK_BODY = b'{"status":"ok"}'
_STALE_BODY = b'{"status":"stale"}'
_NOT_FOUND_BODY = b'{"status":"not found"}'


class Heartbeat:
    """The loop's liveness marker: one monotonic timestamp, re-stamped per cycle."""

    def __init__(self) -> None:
        self.last_beat: float = time.monotonic()  # process start counts as fresh

    def beat(self) -> None:
        self.last_beat = time.monotonic()

    def age_seconds(self) -> float:
        return time.monotonic() - self.last_beat


AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def make_healthz_app(heartbeat: Heartbeat, threshold_seconds: float) -> AsgiApp:
    """Build the ASGI app: 200 fresh / 503 stale on ``GET /healthz``; 404 elsewhere."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - uvicorn lifespan scope
            return
        if scope["path"] != HEALTH_PATH:
            status, body = 404, _NOT_FOUND_BODY
        elif heartbeat.age_seconds() <= threshold_seconds:
            status, body = 200, _OK_BODY
        else:
            status, body = 503, _STALE_BODY
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app
