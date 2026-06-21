"""Subscriber message routing: terminal -> ack, transient -> nack (redelivery
converges via idempotency), malformed envelope -> ack after the loud error."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from csv_ingest_worker.pipeline import IngestOutcome
from csv_ingest_worker.subscriber import Subscriber, process_message
from dis_core.errors import (
    EventPathMismatchError,
    PiiBackendNotConfiguredError,
)

_CONTRACTS = Path(__file__).resolve().parents[3].parent / "contracts" / "pubsub"
_EXAMPLE_BYTES = (_CONTRACTS / "csv.received.example.json").read_bytes()
_EXAMPLE = json.loads(_EXAMPLE_BYTES)


class _StubPipeline:
    """A pipeline stand-in: returns an outcome, or raises what it is given."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.processed: list[Any] = []

    async def process(self, event: Any) -> IngestOutcome:
        self.processed.append(event)
        if self._raises is not None:
            raise self._raises
        return IngestOutcome(disposition="ingested", trace_id=UUID(_EXAMPLE["trace_id"]), bronze_id=None)


async def test_success_acks() -> None:
    pipeline = _StubPipeline()
    assert await process_message(pipeline, _EXAMPLE_BYTES) == "ack"  # type: ignore[arg-type]
    assert len(pipeline.processed) == 1


async def test_malformed_envelope_acks_without_processing() -> None:
    # Terminal: a redelivery of the same malformed envelope fails identically.
    pipeline = _StubPipeline()
    assert await process_message(pipeline, b"not json at all") == "ack"  # type: ignore[arg-type]
    assert pipeline.processed == []


@pytest.mark.parametrize(
    "terminal",
    [
        EventPathMismatchError("path disagrees", field="tenant_id"),
        PiiBackendNotConfiguredError("pii with no backend", columns=("customer_email",)),
    ],
)
async def test_terminal_failures_ack(terminal: Exception) -> None:
    pipeline = _StubPipeline(raises=terminal)
    assert await process_message(pipeline, _EXAMPLE_BYTES) == "ack"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "transient",
    [
        ConnectionError("postgres unreachable"),
        RuntimeError("gcs emulator down"),
        OSError("publish transport failure"),
    ],
)
async def test_transient_failures_nack_for_redelivery(transient: Exception) -> None:
    pipeline = _StubPipeline(raises=transient)
    assert await process_message(pipeline, _EXAMPLE_BYTES) == "nack"  # type: ignore[arg-type]


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


async def test_run_forever_beats_heartbeat_each_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    # Slice 40a: the heartbeat is written by the LOOP, unconditionally — no toggle,
    # no server involved. One cycle (poll_once cancels out of the infinite loop)
    # must advance last_beat.
    from google.cloud import pubsub_v1

    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:9")
    monkeypatch.setattr(pubsub_v1, "SubscriberClient", _RecordingSubscriberClient)
    subscriber = Subscriber(project_id="local-dis", pipeline=_StubPipeline())  # type: ignore[arg-type]
    subscriber.heartbeat.last_beat -= 1000.0  # make any beat observable

    async def _cancel_out(*args: Any, **kwargs: Any) -> int:
        raise asyncio.CancelledError

    monkeypatch.setattr(subscriber, "poll_once", _cancel_out)
    stale_before = subscriber.heartbeat.last_beat
    with pytest.raises(asyncio.CancelledError):
        await subscriber.run_forever()
    assert subscriber.heartbeat.last_beat > stale_before


def test_subscriber_constructs_without_emulator_var_ambient_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Slice 40a emulator-or-ambient: the branch that pre-40a raised now constructs
    # a bare client (ambient ADC = pass nothing; pubsub_v1 honours the env var
    # natively, so both branches are the identical no-kwargs construction).
    from google.cloud import pubsub_v1

    monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)
    monkeypatch.setattr(pubsub_v1, "SubscriberClient", _RecordingSubscriberClient)
    Subscriber(project_id="real-project", pipeline=_StubPipeline())  # type: ignore[arg-type]
    client = _RecordingSubscriberClient.last
    assert client is not None
    assert client.args == ()
    assert client.kwargs == {}
