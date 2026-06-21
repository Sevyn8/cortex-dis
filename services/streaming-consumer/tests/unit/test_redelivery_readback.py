"""The RETRIED readback degrades, never wedges (Slice 30b).

``_seen_before`` is an audit-side concern, so it inherits the fire-and-forget
posture: a failing readback (engine down, table missing) returns ``False`` —
the intake emits SUCCESS and the data path continues. delivery_attempt (post-DLQ
Pub/Sub) is the eventual transport-level replacement; until then this is the
documented best-effort behaviour, proven here against a broken engine.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from dis_quarantine import PostgresQuarantineWriter
from streaming_consumer.orchestrate import ConsumerPipeline
from streaming_consumer.sinks.audit import ConsumerAudit
from streaming_consumer.sinks.quarantine import ConsumerQuarantine

_TENANT = UUID("019e5e3c-b5d3-705f-9002-2451c4ca2626")
_TRACE = UUID("019e99bb-f661-7f22-b21c-aac635797592")


class _NullWriter:
    async def write(self, event: object) -> bool:  # pragma: no cover - never called here
        return True


async def test_seen_before_degrades_to_false_on_readback_failure() -> None:
    pipeline = ConsumerPipeline(
        engine=cast(AsyncEngine, object()),  # any rls_session use explodes
        storage=cast("object", None),  # type: ignore[arg-type]
        audit=ConsumerAudit(_NullWriter()),
        quarantine=ConsumerQuarantine(PostgresQuarantineWriter(cast(AsyncEngine, object()))),
        bronze_bucket="unused",
    )
    assert await pipeline._seen_before(_TENANT, _TRACE) is False  # noqa: SLF001
