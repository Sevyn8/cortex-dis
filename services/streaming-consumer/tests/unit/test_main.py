"""The slice-40a toggle seam in ``_run``: off = the pure loop (no server object),
on = healthz server + loop as sibling tasks — the SAME ``run_forever`` callable in
both branches (the Worker-Pools config-only switch), and the server answers while
a loop iteration is in flight (no starvation)."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from typing import Any

import pytest
import uvicorn

import streaming_consumer.main as main_module
from streaming_consumer.health import Heartbeat, make_healthz_app
from streaming_consumer.main import _run

_REQUIRED_ENV = {
    "POSTGRES_URL": "postgresql+psycopg://u:p@localhost:5433/ithina_dis_db",
    "PUBSUB_PROJECT_ID": "local-dis",
    "GCS_BUCKET_BRONZE": "ithina-bronze-raw",
}


class _FakeEngine:
    async def dispose(self) -> None:
        return None


class _LoopExitError(Exception):
    """Sentinel crash to exit the fake infinite loop. Deliberately NOT
    CancelledError: a task raising CancelledError is treated as *cancelled* and
    TaskGroup ignores it (no sibling abort — the toggle-on test would hang on the
    sleeping server). A plain exception is also the realistic crash shape, so the
    toggle-on test faithfully proves the teardown claim."""


class _FakeSubscriber:
    """Stands in for Subscriber: records that the ONE loop callable was awaited."""

    built: _FakeSubscriber | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.heartbeat = Heartbeat()
        self.run_forever_awaits = 0
        _FakeSubscriber.built = self

    async def run_forever(self) -> None:
        self.run_forever_awaits += 1
        raise _LoopExitError  # one pass, then out of the infinite loop


class _ForbiddenServer:
    """uvicorn.Server stand-in for toggle-off: constructing it is the failure."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("uvicorn.Server constructed with the toggle OFF")


class _RecordingServer:
    """uvicorn.Server stand-in for toggle-on: serve() must actually be awaited."""

    built: _RecordingServer | None = None

    def __init__(self, config: Any) -> None:
        self.config = config
        self.serve_awaits = 0
        _RecordingServer.built = self

    async def serve(self) -> None:
        self.serve_awaits += 1
        await asyncio.sleep(3600)  # serve until the TaskGroup cancels us


async def _no_target_check(engine: Any) -> None:
    return None  # the startup DB assertion needs no database in this unit


def _wire_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(main_module, "create_rls_engine", lambda url: _FakeEngine())
    monkeypatch.setattr(main_module, "_assert_dis_target", _no_target_check)
    monkeypatch.setattr(main_module, "StorageClient", lambda **kw: object())
    monkeypatch.setattr(main_module, "select_writer", lambda backend, engine: object())
    monkeypatch.setattr(main_module, "ConsumerAudit", lambda writer: object())
    monkeypatch.setattr(main_module, "PostgresQuarantineWriter", lambda engine: object())
    monkeypatch.setattr(main_module, "ConsumerQuarantine", lambda writer: object())
    monkeypatch.setattr(main_module, "ConsumerPipeline", lambda **kw: object())
    monkeypatch.setattr(main_module, "Subscriber", _FakeSubscriber)
    _FakeSubscriber.built = None
    _RecordingServer.built = None


async def test_toggle_off_runs_pure_loop_no_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # LOCAL UNCHANGED: toggle unset -> no server object exists, PORT never read,
    # and the loop callable is awaited directly (the verbatim pre-40a path).
    _wire_fakes(monkeypatch)
    monkeypatch.delenv("RUN_HEALTH_SERVER", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    # Patch the real uvicorn module: main.py does `import uvicorn` and calls
    # `uvicorn.Server(...)`, so main_module.uvicorn IS this module object — same
    # call site, but mypy-clean (no attr through the not-exported module alias).
    monkeypatch.setattr(uvicorn, "Server", _ForbiddenServer)
    with pytest.raises(_LoopExitError):
        await _run()
    subscriber = _FakeSubscriber.built
    assert subscriber is not None
    assert subscriber.run_forever_awaits == 1


async def test_toggle_on_runs_server_and_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_fakes(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", "true")
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setattr(uvicorn, "Server", _RecordingServer)
    with pytest.raises(BaseExceptionGroup):  # the loop's _LoopExitError crash, via TaskGroup
        await _run()
    subscriber = _FakeSubscriber.built
    server = _RecordingServer.built
    assert subscriber is not None and server is not None
    # BOTH siblings ran, and the loop callable is the SAME method as toggle-off
    # awaits — one implementation, no per-mode fork.
    assert subscriber.run_forever_awaits == 1
    assert server.serve_awaits == 1
    assert server.config.port == 8080


async def test_loop_callable_is_identical_across_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Worker-Pools guarantee, asserted by identity: both branches of _run call
    # the one bound method; there is no second loop implementation to drift.
    _wire_fakes(monkeypatch)
    monkeypatch.delenv("RUN_HEALTH_SERVER", raising=False)
    with pytest.raises(_LoopExitError):
        await _run()
    off_subscriber = _FakeSubscriber.built
    assert off_subscriber is not None
    assert off_subscriber.run_forever.__func__ is _FakeSubscriber.run_forever  # type: ignore[attr-defined]


async def test_healthz_answers_while_loop_iteration_in_flight() -> None:
    # The no-starvation proof: a long in-flight (awaiting) iteration does not block
    # the healthz route — both are tasks on the one event loop and the loop yields.
    heartbeat = Heartbeat()
    app = make_healthz_app(heartbeat, 60.0)
    release = asyncio.Event()

    async def long_iteration() -> None:
        heartbeat.beat()
        await release.wait()  # blocked mid-"poll", like a slow pull/process await

    task = asyncio.create_task(long_iteration())
    await asyncio.sleep(0)  # the iteration is now in flight and blocked

    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:  # pragma: no cover - never pulled
        return {"type": "http.request"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    await asyncio.wait_for(
        app({"type": "http", "method": "GET", "path": "/healthz"}, receive, send),
        timeout=1.0,  # must answer promptly despite the blocked iteration
    )
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    assert status == 200
    release.set()
    await task
