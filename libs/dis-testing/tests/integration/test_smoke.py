"""End-to-end smoke tests against the live local stack.

Acceptance criterion 7: a single test obtains a JWT from the CM fake, resolves it
via the Identity Service fake, and reads the corresponding seeded tenant from
``identity_mirror`` — end to end. Also exercises the real ``identity.changed``
emission through the Pub/Sub emulator (criterion 3, end-to-end).

All fixtures come from the dis-testing plugin and skip when the stack is down.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import Engine, text

from dis_core.identity import HttpIdentityClient
from dis_testing import fixtures as fx
from dis_testing.pubsub import pubsub_stack_project

pytestmark = pytest.mark.integration


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "contracts" / "pubsub").is_dir():
            return parent
    raise RuntimeError("could not locate repo root")


async def test_jwt_resolve_then_read_seeded_tenant(
    cm_jwt: str, identity_client: HttpIdentityClient, seeded_identity: Engine
) -> None:
    # 1. Resolve the CM-issued JWT through the Identity Service fake. The answer
    #    carries the internal UUIDs directly (D37) plus the authoritative codes.
    identity = await identity_client.resolve_from_token(cm_jwt)
    assert identity.tenant_id == fx.PRIMARY_TENANT.uuid
    assert identity.store_id == fx.PRIMARY_STORE.uuid
    assert identity.display_code == fx.PRIMARY_TENANT.display_code

    # 2. No external→internal bridge needed: the resolved UUID IS the DB key (D37).
    tenant_uuid = identity.tenant_id

    # 3. Read the corresponding seeded tenant from identity_mirror.
    with seeded_identity.connect() as conn:
        row = conn.execute(
            text("SELECT name, status FROM identity_mirror.tenants WHERE tenant_id = :tid"),
            {"tid": str(tenant_uuid)},
        ).first()

    assert row is not None, "resolved identity has no matching seeded identity_mirror row"
    assert row.name == fx.PRIMARY_TENANT.name
    assert row.status == fx.PRIMARY_TENANT.status


async def test_validate_against_seeded_pair(
    identity_client: HttpIdentityClient, seeded_identity: Engine
) -> None:
    result = await identity_client.validate(fx.PRIMARY_TENANT.uuid, fx.PRIMARY_STORE.uuid)
    assert result.exists is True
    assert result.is_active is True


def test_identity_changed_published_to_emulator(customer_master_url: str) -> None:
    """The CM fake publishes a real identity.changed message the emulator delivers."""
    import httpx
    import uuid_utils
    from google.api_core.exceptions import GoogleAPICallError
    from google.cloud import pubsub_v1

    if not os.environ.get("PUBSUB_EMULATOR_HOST"):
        pytest.skip("PUBSUB_EMULATOR_HOST not set")

    # The CM fake (a docker container) publishes identity.changed on the STACK
    # project (local-dis), not the in-process test project — so subscribe there.
    # Safe: no resident subscribes to identity.changed (D100 isolation is about
    # csv.received / ingress.ready, which residents do consume).
    project = pubsub_stack_project()
    subscriber = pubsub_v1.SubscriberClient()
    topic_path = subscriber.topic_path(project, "identity.changed")
    sub_path = subscriber.subscription_path(project, f"smoke-{uuid_utils.uuid7()}")

    # Subscription created BEFORE publish, so it only receives our message.
    subscriber.create_subscription(request={"name": sub_path, "topic": topic_path})
    try:
        resp = httpx.post(
            f"{customer_master_url}/v1/changes",
            json={
                "entity": "tenant",
                "code": fx.PRIMARY_TENANT.display_code,
                "event_type": "updated",
            },
            timeout=5.0,
        )
        resp.raise_for_status()

        received: list[dict[str, Any]] = []
        for _ in range(10):
            try:
                pull = subscriber.pull(request={"subscription": sub_path, "max_messages": 10}, timeout=5)
            except GoogleAPICallError:
                break
            for msg in pull.received_messages:
                received.append(json.loads(msg.message.data))
            if received:
                ack_ids = [m.ack_id for m in pull.received_messages]
                subscriber.acknowledge(request={"subscription": sub_path, "ack_ids": ack_ids})
                break

        assert received, "no identity.changed message delivered by the emulator"
        schema = json.loads(
            (_repo_root() / "contracts" / "pubsub" / "identity.changed.schema.json").read_text()
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        ours = [m for m in received if m["entity_id"] == str(fx.PRIMARY_TENANT.uuid)]
        assert ours, "our published message was not among those delivered"
        for message in ours:
            validator.validate(message)
    finally:
        subscriber.delete_subscription(request={"subscription": sub_path})
        subscriber.close()


def test_smoke_uses_only_the_client_interface() -> None:
    # Drop-in evidence (criterion 8): consumers depend on the Protocol, and the
    # concrete HttpIdentityClient is configured purely by URL — the real Slice 13
    # service swaps in behind the same IDENTITY_SERVICE_URL with no test change.
    from dis_core.identity import IdentityClient

    assert isinstance(HttpIdentityClient("http://identity-service-fake"), IdentityClient)
