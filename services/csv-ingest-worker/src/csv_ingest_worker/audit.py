"""Per-stage fire-and-forget audit emission (hard rule 11, D43, D44).

One thin wrapper over the ``dis-audit`` writer. Stage vocabulary is dis-audit's
CLOSED enum — the worker adds no members. The mapping for this service:

- intake / path cross-check / preflight outcome → ``Stage.RECEIVED``
  (preflight detail rides ``event_data``; there is deliberately no PREFLIGHT member)
- the idempotent no-op → ``Stage.RECEIVED`` + ``Outcome.DUPLICATE_NOOP`` with
  ``prior_trace_id`` as a COLUMN (Slice 30c, the D42 revision); the resume
  publish → ``Outcome.RETRIED`` (a retry-completion made legible)
- the PII gate → ``Stage.PII_TOKENIZED``
- the bronze write → ``Stage.BRONZE_WRITTEN``
- the ``ingress.ready`` publish → ``Stage.INGRESS_PUBLISHED``

Every event carries the known ``tenant_id`` (D43 — identity is on the csv.received
event from the first stage), the read ``trace_id``, and the load-bearing id
(``data_ingress_event_id`` where a bronze row exists). Failures in emission are
logged and NEVER raised — the one sanctioned swallow (code-quality rule 6) — and
never block the data path. Duplicate audit rows are tolerated (D44). A swallowed
audit failure is logged as alert-worthy (the Slice 6 D45 mitigation).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from csv_ingest_worker.config import SERVICE_NAME
from dis_audit import AuditEvent, AuditWriter, EventScope, Outcome, Stage
from dis_core.logging import get_logger
from dis_core.timestamps import now_utc

_log = get_logger(SERVICE_NAME)


class WorkerAudit:
    """Builds and emits this worker's stage events, fire-and-forget."""

    def __init__(self, writer: AuditWriter) -> None:
        self._writer = writer

    async def emit(
        self,
        *,
        stage: Stage,
        outcome: Outcome,
        tenant_id: UUID,
        trace_id: UUID,
        bronze_id: UUID | None = None,
        prior_trace_id: UUID | None = None,
        row_count: int | None = None,
        duration_ms: int | None = None,
        event_data: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        """Emit one stage event. Never raises; never blocks the data path.

        ``failure_code`` takes a :class:`~dis_audit.FailureCode` member (a
        ``StrEnum``, Slice 30b stable vocabulary); ``duration_ms`` is the
        pipeline's lap-timer stage span.
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
                event_scope=EventScope.INGRESS_EVENT,
                outcome=outcome,
                row_count=row_count,
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
