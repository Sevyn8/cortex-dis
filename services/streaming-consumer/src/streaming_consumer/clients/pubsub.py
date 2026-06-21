"""The ingress.ready subscriber: pull loop + the audit-and-nack disposition.

Startup REQUIRES the subscription to exist and raises loudly if it does not —
provisioning lives in ``tools/local/create_topics.py`` (``make topics-create``),
NEVER in consumer runtime code. The client is emulator-or-ambient (slice 40a): the
emulator when ``PUBSUB_EMULATOR_HOST`` is set (the ``pubsub_v1`` client honours it
natively), real Pub/Sub via ambient service-account credentials when it is not.

Message routing (the Slice 10 disposition + the Slice 11a quarantine carve-out —
see orchestrate.py):

- ``written`` → ack.
- ``quarantined`` → **ack**. The chunk is HELD in the quarantine store (the
  fail-loud write already succeeded) and the QUARANTINED audit emitted; the ack
  is the storm fix — the deterministic-failure redeliver loop is broken at its
  source.
- any failed disposition or any raised pipeline error → **nack** (the pipeline
  already emitted the FAILURE audit; bronze remains the recoverable source, D5).
  This includes a FAILED QUARANTINE WRITE (the pipeline falls back to raise /
  ``failed_*`` so the held data is never acked-and-lost) and the self-heal
  exclusions (``HOT_POSITION_MISSING``, the store-miss contract violation) —
  redelivery is their designed recovery; the Pub/Sub dead-letter policy
  backstops.
- the ONE pre-pipeline ack-on-failure: an unparseable envelope
  (``EventContractError`` at parse). Identity may be unknowable there, so a
  D43-conformant audit row may be impossible, and a redelivery fails identically
  (the 9b precedent). No quarantine either — the tables' ``tenant_id`` is NOT
  NULL (unchanged in 11a).

No ordering key is consumed: D60 resolved as STRIKE (canonical correctness is
event-time-based — D33 read-time dedup + the D64 conditional upsert; an ordering
key would defend nothing the consumer needs).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from dis_core.errors import DisError, EventContractError
from dis_core.logging import get_logger
from streaming_consumer.config import INGRESS_READY_SUBSCRIPTION, SERVICE_NAME
from streaming_consumer.envelope import parse_ingress_ready
from streaming_consumer.health import Heartbeat
from streaming_consumer.orchestrate import ConsumerPipeline

if TYPE_CHECKING:
    from google.cloud import pubsub_v1

_log = get_logger(SERVICE_NAME)

Decision = Literal["ack", "nack"]


async def process_message(pipeline: ConsumerPipeline, data: bytes) -> Decision:
    """Route one raw message body: parse, process, decide ack/nack."""
    try:
        event = parse_ingress_ready(data)
    except EventContractError as exc:
        _log.bind(stage="intake", tenant_id=exc.tenant_id, trace_id=exc.trace_id).error(
            "ingress.ready envelope rejected (terminal): %s", exc
        )
        return "ack"

    log = _log.bind(stage="intake", tenant_id=str(event.tenant_id), trace_id=str(event.trace_id))
    try:
        outcome = await pipeline.process(event)
    except Exception as exc:
        log.error("chunk failed (nacked; FAILURE audit emitted — audit-and-nack): %s", exc)
        return "nack"
    if outcome.disposition == "quarantined":
        # The Slice 11a storm fix: the chunk is held (fail-loud write succeeded),
        # so the ack breaks the redeliver loop at its source.
        log.info("chunk quarantined (acked; held in quarantine.* with QUARANTINED audit)")
        return "ack"
    if outcome.disposition != "written":
        log.error("chunk failed validation: %s (nacked — audit-and-nack)", outcome.disposition)
        return "nack"
    log.info("processed: %s", outcome.disposition)
    return "ack"


@dataclass
class Subscriber:
    """The long-running pull loop over the consumer's ingress.ready subscription."""

    project_id: str
    pipeline: ConsumerPipeline
    max_messages: int = 10
    # Slice 40a: beaten once per loop cycle UNCONDITIONALLY (all modes — the loop
    # never branches on environment; only the healthz SERVER is toggled, main.py).
    heartbeat: Heartbeat = field(default_factory=Heartbeat)

    def __post_init__(self) -> None:
        import os

        mode = "emulator" if os.environ.get("PUBSUB_EMULATOR_HOST") else "ambient"
        _log.bind(stage="startup").info("pubsub subscriber constructed", extra={"pubsub_mode": mode})
        from google.cloud import pubsub_v1

        self._client = pubsub_v1.SubscriberClient()
        self._sub_path = self._client.subscription_path(self.project_id, INGRESS_READY_SUBSCRIPTION)
        self._require_subscription()

    def _require_subscription(self) -> None:
        """Fail loud at startup when the subscription is absent (errors, never skips)."""
        from google.api_core.exceptions import NotFound

        try:
            self._client.get_subscription(request={"subscription": self._sub_path})
        except NotFound as exc:
            raise DisError(
                f"subscription {INGRESS_READY_SUBSCRIPTION!r} does not exist on project "
                f"{self.project_id!r}; run `make topics-create` to provision it"
            ) from exc

    def _pull(self) -> Any:
        return self._client.pull(
            request={"subscription": self._sub_path, "max_messages": self.max_messages},
            timeout=10,
        )

    async def poll_once(self) -> int:
        """One pull + process + ack/nack pass. Returns the number of messages handled."""
        response = await asyncio.to_thread(self._pull)
        ack_ids: list[str] = []
        nack_ids: list[str] = []
        received: list[pubsub_v1.types.ReceivedMessage] = list(response.received_messages)
        for message in received:
            decision = await process_message(self.pipeline, message.message.data)
            (ack_ids if decision == "ack" else nack_ids).append(message.ack_id)
        if ack_ids:
            await asyncio.to_thread(
                self._client.acknowledge,
                request={"subscription": self._sub_path, "ack_ids": ack_ids},
            )
        if nack_ids:
            # Deadline 0 = immediate redelivery (the emulator honours it); D33
            # read-time dedup + the D64 conditional upsert absorb the replay.
            await asyncio.to_thread(
                self._client.modify_ack_deadline,
                request={
                    "subscription": self._sub_path,
                    "ack_ids": nack_ids,
                    "ack_deadline_seconds": 0,
                },
            )
        return len(received)

    async def run_forever(self) -> None:
        log = _log.bind(stage="subscriber")
        log.info("subscribed; pulling from %s", self._sub_path)
        while True:
            # Slice 40a: the readiness heartbeat, written unconditionally in every
            # mode (local / Cloud Run Service / Worker Pools) — only the healthz
            # server that READS it is toggled. A dead/hung loop stops beating and
            # /healthz goes stale.
            self.heartbeat.beat()
            try:
                await self.poll_once()
            except Exception:
                # The loop must survive transient pull/transport errors; each
                # message's own routing already decided ack/nack.
                log.exception("poll pass failed; retrying")
                await asyncio.sleep(1)
