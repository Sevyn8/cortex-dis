"""PubsubPublisher emulator-or-ambient construction (slice 40a).

The pubsub_v1 client honours PUBSUB_EMULATOR_HOST natively, so BOTH branches
construct the same bare ``PublisherClient()`` — the slice deleted the
emulator-required guard; these tests pin that construction succeeds in both
modes and stays the no-kwargs ambient shape (no endpoint, no credentials —
ambient ADC is "pass nothing", the dis-storage posture).
"""

from __future__ import annotations

import pytest
from google.cloud import pubsub_v1

from dis_ui_server.publisher import PubsubPublisher


class _RecordingClient:
    """Stands in for pubsub_v1.PublisherClient; records construction kwargs."""

    instances: list[_RecordingClient] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        _RecordingClient.instances.append(self)


@pytest.fixture(autouse=True)
def _patched_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _RecordingClient.instances = []
    monkeypatch.setattr(pubsub_v1, "PublisherClient", _RecordingClient)


def test_constructs_without_emulator_var_ambient_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The branch that pre-40a raised: no emulator var -> bare client, no raise."""
    monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)
    PubsubPublisher(project_id="real-project")
    (client,) = _RecordingClient.instances
    # Ambient = pass nothing: no endpoint, no credentials kwargs.
    assert client.args == ()
    assert client.kwargs == {}


def test_constructs_with_emulator_var_same_bare_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local path unchanged: emulator var set -> the identical bare construction."""
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")
    PubsubPublisher(project_id="dis-local")
    (client,) = _RecordingClient.instances
    assert client.args == ()
    assert client.kwargs == {}
