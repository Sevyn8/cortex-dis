"""Pub/Sub publishing for the fakes, plus an in-memory publisher for unit tests.

Two implementations behind one ``Publisher`` protocol:

  * :class:`EmulatorPublisher` — publishes to the local Pub/Sub emulator (the path
    ``make run-local`` uses). Refuses to run unless ``PUBSUB_EMULATOR_HOST`` is set,
    so it can never hit real Pub/Sub from a fake.
  * :class:`InMemoryPublisher` — records published messages in a list; for pure
    unit tests with no emulator running.

Topic names are the dotted contract names (e.g. ``identity.changed``), matching
``tools/local/create_topics.py``.
"""

from __future__ import annotations

import os
from typing import Protocol

DEFAULT_PROJECT_ID = "local-dis"


class Publisher(Protocol):
    """Minimal publish surface the fakes need."""

    def publish(self, topic_name: str, data: bytes) -> str:
        """Publish ``data`` to ``topic_name``; return the message id."""
        ...


class InMemoryPublisher:
    """Records published messages instead of sending them. Test-only."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    def publish(self, topic_name: str, data: bytes) -> str:
        self.published.append((topic_name, data))
        return f"in-memory-{len(self.published)}"

    def messages_for(self, topic_name: str) -> list[bytes]:
        return [data for topic, data in self.published if topic == topic_name]


class EmulatorPublisher:
    """Publishes to the local Pub/Sub emulator via google-cloud-pubsub."""

    def __init__(self, project_id: str | None = None) -> None:
        if not os.environ.get("PUBSUB_EMULATOR_HOST"):
            # Guard: a fake must never publish to real Pub/Sub.
            raise RuntimeError(
                "PUBSUB_EMULATOR_HOST not set; EmulatorPublisher refuses to run against real Pub/Sub"
            )
        # Imported lazily so unit tests that use InMemoryPublisher need no GCP import.
        # (Typing: covered by the google.cloud.* ignore_missing_imports override.)
        from google.cloud import pubsub_v1

        self._project_id = project_id or os.environ.get("PUBSUB_PROJECT_ID", DEFAULT_PROJECT_ID)
        self._client = pubsub_v1.PublisherClient()

    def publish(self, topic_name: str, data: bytes) -> str:
        topic_path = self._client.topic_path(self._project_id, topic_name)
        future = self._client.publish(topic_path, data)
        message_id: str = future.result(timeout=10)  # untyped client boundary -> declared str
        return message_id
