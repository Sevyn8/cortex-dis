"""Resolve a Pub/Sub topic/subscription SHORT name from the environment.

Hard rule 10 still holds: the contract names are frozen. This resolver does NOT
change a name's meaning — it lets DEPLOYMENT override the literal short name a
publish/subscribe call passes, so the app conforms to whatever infra provisioned
(terraform's ``dis-`` prefixed, dots→dashes names) without the app and the infra
drifting apart. The default is the EXACT current local literal that
``tools/local/create_topics.py`` creates, so local dev — which sets no override —
is byte-for-byte unchanged.

The returned value is always a SHORT name (e.g. ``dis-csv-received``,
``dis-csv-received-sub``), never a full ``projects/.../topics/...`` path: every
call site passes it as the second arg to
``client.topic_path(project, name)`` / ``client.subscription_path(project, name)``,
which builds the full path itself.
"""

from __future__ import annotations

import os

from dis_core.errors import DisError


def resolve_pubsub_name(env_var: str, default: str) -> str:
    """Return the override in ``env_var`` if set, else ``default``.

    Set-but-empty RAISES ``DisError`` (code-quality rule 4 and the
    ``cors_allowed_origins_from_env`` precedent): an empty value is an ambiguous
    declaration, not a sanctioned fallback. Unset → the default. Terraform always
    sets a non-empty value; local sets nothing, so the empty branch is defensive.
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    name = raw.strip()
    if not name:
        raise DisError(
            f"{env_var} is set but empty; unset it for the default {default!r} or set the provisioned name"
        )
    return name


# The frozen-contract (hard rule 10) local provisioning set. This is the ONE place
# the topic/subscription NAMES are defined: tools/local/create_topics.py (the
# `make topics-create` CLI) and the dis-testing pytest harness (the test-scoped
# project, D100 structural isolation) both provision from it, so the tool and the
# tests can never drift on names.
PUBSUB_TOPICS: tuple[str, ...] = (
    "csv.received",
    "ingress.ready",
    "ingress.resubmit",
    "identity.changed",
    "quarantine",
    "mapping.changed",
    "pipeline.dlq",
)

# subscription id -> topic. The csv-ingest-worker pulls csv.received from here
# (slice-9b) and the streaming consumer pulls ingress.ready (slice-10); each
# service's config pins its own name as a frozen constant.
PUBSUB_SUBSCRIPTIONS: dict[str, str] = {
    "csv-ingest-worker.csv.received": "csv.received",
    "streaming-consumer.ingress.ready": "ingress.ready",
}


def provision_pubsub(project_id: str) -> None:
    """Idempotently create the DIS topics + worker subscriptions on ``project_id``.

    Existing topics/subscriptions are skipped (``AlreadyExists``). The real-GCP
    guard is the CALLER's responsibility (both callers only ever pass a local
    emulator project, gated on ``PUBSUB_EMULATOR_HOST``): ``create_topics.py``'s
    ``main`` and the dis-testing ``_dis_pubsub_provisioned`` fixture. The
    ``pubsub_v1`` clients honour ``PUBSUB_EMULATOR_HOST`` natively.
    """
    # Lazy import (the dis-core BqClient idiom): a pure name lookup needs no GCP client.
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    for topic_name in PUBSUB_TOPICS:
        topic_path = publisher.topic_path(project_id, topic_name)
        try:
            publisher.create_topic(request={"name": topic_path})
            print(f"created: {topic_name}")
        except AlreadyExists:
            print(f"exists:  {topic_name}")

    subscriber = pubsub_v1.SubscriberClient()
    for subscription_name, topic_name in PUBSUB_SUBSCRIPTIONS.items():
        sub_path = subscriber.subscription_path(project_id, subscription_name)
        topic_path = publisher.topic_path(project_id, topic_name)
        try:
            subscriber.create_subscription(request={"name": sub_path, "topic": topic_path})
            print(f"created: {subscription_name} -> {topic_name}")
        except AlreadyExists:
            print(f"exists:  {subscription_name} -> {topic_name}")
