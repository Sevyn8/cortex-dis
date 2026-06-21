"""Per-stage fire-and-forget audit emission (hard rule 11, D43, D44, D42).

One thin wrapper over the ``dis-audit`` writer. Stage vocabulary is dis-audit's
CLOSED enum — this consumer adds no members. The mapping for this service:

- intake + bronze/GCS fetch → ``Stage.RECEIVED`` (no consumer-fetch member exists;
  ``service_name`` disambiguates from the receivers — registered with D42)
- mapping load + routing → ``Stage.MAPPING_LOOKED_UP``
- the two gates → ``Stage.PRE_MAPPING_VALIDATED`` / ``Stage.POST_MAPPING_VALIDATED``
- the engine → ``Stage.MAPPING_EXECUTED``
- the dual-write → ``Stage.CANONICAL_WRITTEN``
- ``Stage.IDENTITY_VALIDATED`` is deliberately NEVER emitted: no Identity Service
  call exists (D28/Slice 13); the composite FK is the enforcement (D39).

**Duplicate representation (the D42 REVISION, Slice 30c)**: a dedup-key hit emits
a ROW-scoped ``CANONICAL_WRITTEN`` event whose ``outcome`` IS the kind —
``DUPLICATE_NOOP`` | ``DUPLICATE_OVERWRITTEN`` (both refine SUCCESS: the
append-only insert genuinely landed, D33) — with ``prior_trace_id`` as a
first-class column. Slice 10's deliberate event_data-JSONB resolution is
superseded for console queryability; the non-queried detail stays in
``event_data``::

    {"row_hash": …,
     "dedup_key": {"store_id": …, "source_id": …, "source_event_id": …}}

Every event carries the known ``tenant_id`` (D43), the read ``trace_id``,
``mapping_version_id`` where known (D22 context), and the bronze id as the
load-bearing id. Failures in emission are logged and NEVER raised (the one
sanctioned swallow); duplicates are tolerated (D44).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from dis_audit import AuditEvent, AuditWriter, EventScope, Outcome, Stage
from dis_core.logging import get_logger
from dis_core.timestamps import now_utc
from streaming_consumer.config import SERVICE_NAME
from streaming_consumer.sinks.canonical import DuplicateHit

_log = get_logger(SERVICE_NAME)


class ConsumerAudit:
    """Builds and emits this consumer's stage events, fire-and-forget."""

    def __init__(self, writer: AuditWriter) -> None:
        self._writer = writer

    async def emit(
        self,
        *,
        stage: Stage,
        outcome: Outcome,
        tenant_id: UUID,
        trace_id: UUID,
        scope: EventScope = EventScope.INGRESS_EVENT,
        bronze_id: UUID | None = None,
        prior_trace_id: UUID | None = None,
        mapping_version_id: int | None = None,
        row_count: int | None = None,
        rows_succeeded: int | None = None,
        rows_failed: int | None = None,
        row_offset: int | None = None,
        duration_ms: int | None = None,
        event_data: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        """Emit one stage event. Never raises; never blocks the data path.

        ``failure_code`` takes a :class:`~dis_audit.FailureCode` member (a
        ``StrEnum``, so the parameter stays ``str``-typed) — the Slice 30b
        stable vocabulary; ``duration_ms`` is the orchestrator's lap-timer
        stage span.
        """
        log = _log.bind(stage=str(stage.value), tenant_id=str(tenant_id), trace_id=str(trace_id))
        try:
            event = AuditEvent(
                event_timestamp=now_utc(),
                trace_id=trace_id,
                prior_trace_id=prior_trace_id,
                tenant_id=tenant_id,
                data_ingress_event_id=bronze_id,
                service_name=SERVICE_NAME,
                stage=stage,
                event_scope=scope,
                outcome=outcome,
                mapping_version_id=mapping_version_id,
                row_count=row_count,
                rows_succeeded=rows_succeeded,
                rows_failed=rows_failed,
                row_offset=row_offset,
                duration_ms=duration_ms,
                event_data=event_data,
                failure_code=failure_code,
                failure_message=failure_message,
            )
            written = await self._writer.write(event)
            if not written:
                # The writer already logged its own failure detail; this line is the
                # service-side alert-worthy marker (D45 silent-loss mitigation).
                log.error("audit write reported failure; data path continues (hard rule 11)")
        except Exception:  # noqa: BLE001 - the ONE sanctioned swallow (hard rule 11)
            log.exception("audit emission raised; swallowed so the data path continues")

    async def emit_duplicate(
        self,
        hit: DuplicateHit,
        *,
        tenant_id: UUID,
        store_id: UUID,
        source_id: str,
        trace_id: UUID,
        bronze_id: UUID,
        mapping_version_id: int,
    ) -> None:
        """One ROW-scoped duplicate event — the column representation (D42 revision).

        ``hit.kind`` is exactly the ``DUPLICATE_NOOP`` | ``DUPLICATE_OVERWRITTEN``
        vocabulary, so the outcome IS the kind (both refine SUCCESS — the
        append-only insert landed, D33). Only the queried-by fields are columns;
        ``row_hash`` and ``dedup_key`` stay in ``event_data``.
        """
        await self.emit(
            stage=Stage.CANONICAL_WRITTEN,
            outcome=Outcome(hit.kind),
            scope=EventScope.ROW,
            tenant_id=tenant_id,
            trace_id=trace_id,
            prior_trace_id=hit.prior_trace_id,
            bronze_id=bronze_id,
            mapping_version_id=mapping_version_id,
            row_offset=hit.chunk_row_index,
            event_data={
                "row_hash": hit.row_hash,
                "dedup_key": {
                    "store_id": str(store_id),
                    "source_id": source_id,
                    "source_event_id": hit.source_event_id,
                },
            },
        )
