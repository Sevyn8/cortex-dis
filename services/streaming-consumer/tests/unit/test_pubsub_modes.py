"""Subscriber emulator-or-ambient construction (slice 40a).

The pubsub_v1 client honours PUBSUB_EMULATOR_HOST natively, so BOTH branches
construct the same bare ``SubscriberClient()`` — the slice deleted the
emulator-required guard; this pins that the no-emulator branch (pre-40a a raise)
constructs the no-kwargs ambient shape.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from google.cloud import pubsub_v1

from streaming_consumer.clients.pubsub import Subscriber


class _RecordingSubscriberClient:
    """Stands in for pubsub_v1.SubscriberClient; records construction kwargs."""

    last: _RecordingSubscriberClient | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        _RecordingSubscriberClient.last = self

    def subscription_path(self, project: str, name: str) -> str:
        return f"projects/{project}/subscriptions/{name}"

    def get_subscription(self, request: dict[str, Any]) -> None:
        return None  # exists; the startup existence check passes


def test_subscriber_constructs_without_emulator_var_ambient_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)
    monkeypatch.setattr(pubsub_v1, "SubscriberClient", _RecordingSubscriberClient)
    Subscriber(project_id="real-project", pipeline=object())  # type: ignore[arg-type]
    client = _RecordingSubscriberClient.last
    assert client is not None
    # Ambient = pass nothing: no endpoint, no credentials kwargs.
    assert client.args == ()
    assert client.kwargs == {}


async def test_run_forever_beats_heartbeat_each_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    # Slice 40a: the heartbeat is written by the LOOP, unconditionally — no toggle,
    # no server involved. One cycle (poll_once cancels out of the infinite loop)
    # must advance last_beat.
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:9")
    monkeypatch.setattr(pubsub_v1, "SubscriberClient", _RecordingSubscriberClient)
    subscriber = Subscriber(project_id="local-dis", pipeline=object())  # type: ignore[arg-type]
    subscriber.heartbeat.last_beat -= 1000.0  # make any beat observable

    async def _cancel_out(*args: Any, **kwargs: Any) -> int:
        raise asyncio.CancelledError

    monkeypatch.setattr(subscriber, "poll_once", _cancel_out)
    stale_before = subscriber.heartbeat.last_beat
    with pytest.raises(asyncio.CancelledError):
        await subscriber.run_forever()
    assert subscriber.heartbeat.last_beat > stale_before
