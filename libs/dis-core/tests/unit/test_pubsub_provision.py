"""PUBSUB_TOPICS / PUBSUB_SUBSCRIPTIONS / provision_pubsub.

The name lists are the single provisioning source (tools/local/create_topics.py and
the dis-testing pytest harness both read them). These tests pin: the topic set
matches the frozen Pub/Sub contracts; every subscription targets a real topic; and
provision_pubsub is idempotent. The emulator-touching idempotency check is skip-safe
when PUBSUB_EMULATOR_HOST is unset, so a bare `pytest` stays green.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dis_core.pubsub_names import PUBSUB_SUBSCRIPTIONS, PUBSUB_TOPICS, provision_pubsub

# repo-root/contracts/pubsub holds one <topic>.schema.json per frozen topic.
_CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "pubsub"


def test_topics_match_the_frozen_contracts() -> None:
    contract_topics = {p.name[: -len(".schema.json")] for p in _CONTRACTS.glob("*.schema.json")}
    assert set(PUBSUB_TOPICS) == contract_topics, (
        "PUBSUB_TOPICS drifted from contracts/pubsub/*.schema.json — the frozen-contract set"
    )


def test_topics_have_no_duplicates() -> None:
    assert len(PUBSUB_TOPICS) == len(set(PUBSUB_TOPICS))


def test_every_subscription_targets_a_real_topic() -> None:
    for subscription_name, topic_name in PUBSUB_SUBSCRIPTIONS.items():
        assert topic_name in PUBSUB_TOPICS, f"{subscription_name} targets unknown topic {topic_name!r}"


@pytest.mark.skipif(
    not os.environ.get("PUBSUB_EMULATOR_HOST"),
    reason="PUBSUB_EMULATOR_HOST not set — provisioning needs the emulator",
)
def test_provision_pubsub_is_idempotent() -> None:
    from google.cloud import pubsub_v1

    # A throwaway project so the assertion is self-contained and never collides
    # with local-dis / local-dis-test. Emulator projects are free namespaces.
    project = "local-dis-provision-test"

    provision_pubsub(project)
    provision_pubsub(project)  # second call must no-op (AlreadyExists), never raise

    publisher = pubsub_v1.PublisherClient()
    for topic_name in PUBSUB_TOPICS:
        # get_topic raises NotFound if the create did not land; absence fails the test.
        publisher.get_topic(request={"topic": publisher.topic_path(project, topic_name)})

    subscriber = pubsub_v1.SubscriberClient()
    for subscription_name in PUBSUB_SUBSCRIPTIONS:
        subscriber.get_subscription(
            request={"subscription": subscriber.subscription_path(project, subscription_name)}
        )
    subscriber.close()
