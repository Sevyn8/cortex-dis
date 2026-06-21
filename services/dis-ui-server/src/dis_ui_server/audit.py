"""Fire-and-forget audit emission for the CSV-upload receiver (hard rule 11, D43).

The 9b ``WorkerAudit`` pattern, service-named. Stage vocabulary is dis-audit's
CLOSED enum — this service adds no members. The mapping for the upload endpoint:

- an accepted upload (object written, ``csv.received`` published) →
  ``Stage.RECEIVED`` + ``Outcome.SUCCESS`` with ``event_data.phase =
  "csv_upload_phase1"`` — distinguishable from the worker's own RECEIVED row by
  ``service_name`` (the closed-enum gap for a dedicated upload stage is the
  registered D42/D45 follow-up, API_CONTRACT §9).
- a GCS-write or publish failure after identity is resolved →
  ``Stage.RECEIVED`` + ``Outcome.FAILURE`` (tenant + trace are known there).
- 4xx rejections (multipart shape/size, tier-0, unknown/inactive template or
  store) ALSO emit ``Stage.RECEIVED`` + ``Outcome.FAILURE`` (Slice 30b): tenant
  and trace exist before the first gate, so the audit story starts at the
  rejection, with the stable ``FailureCode`` and the step/reason in
  ``event_data``. Emit-then-re-raise: the §2.3 envelope and status codes are
  untouched, and the fire-and-forget emit can never turn a 4xx into a 5xx.

This endpoint is a receiver stage, so events carry the caller context columns
(``auth_principal`` as ``user:{sub}`` per the live bronze column comment's
vocabulary, ``client_ip``). Failures in emission are logged and NEVER raised —
the one sanctioned swallow (code-quality rule 6); duplicates tolerated (D44).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from dis_audit import AuditEvent, AuditWriter, EventScope, Outcome, Stage
from dis_core.logging import get_logger
from dis_core.timestamps import now_utc
from dis_ui_server.config import SERVICE_NAME

_log = get_logger(SERVICE_NAME)


class UiAudit:
    """Builds and emits this service's stage events, fire-and-forget."""

    def __init__(self, writer: AuditWriter) -> None:
        self._writer = writer

    async def emit(
        self,
        *,
        stage: Stage,
        outcome: Outcome,
        tenant_id: UUID,
        trace_id: UUID,
        row_count: int | None = None,
        duration_ms: int | None = None,
        event_data: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
        auth_principal: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        """Emit one stage event. Never raises; never blocks the data path.

        ``failure_code`` takes a :class:`~dis_audit.FailureCode` member (a
        ``StrEnum``); ``duration_ms`` is the handler's whole-request elapsed
        (this endpoint emits one row per request).
        """
        log = _log.bind(stage=str(stage.value), tenant_id=str(tenant_id), trace_id=str(trace_id))
        try:
            event = AuditEvent(
                event_timestamp=now_utc(),
                trace_id=trace_id,
                tenant_id=tenant_id,
                service_name=SERVICE_NAME,
                stage=stage,
                event_scope=EventScope.INGRESS_EVENT,
                outcome=outcome,
                row_count=row_count,
                duration_ms=duration_ms,
                event_data=event_data,
                failure_code=failure_code,
                failure_message=failure_message,
                auth_principal=auth_principal,
                client_ip=client_ip,
            )
            written = await self._writer.write(event)
            if not written:
                # The writer already logged its own failure detail; this line is the
                # service-side alert-worthy marker (D45 silent-loss mitigation).
                log.error("audit write reported failure; data path continues (hard rule 11)")
        except Exception:  # noqa: BLE001 - the ONE sanctioned swallow (hard rule 11)
            log.exception("audit emission raised; swallowed so the data path continues")
