"""The consumer's quarantine sink — builds held records off the envelope + flow context.

The thin wrapper over ``dis-quarantine`` (the ``ConsumerAudit`` pattern): this module
owns the mapping from a failure (the envelope, the ``_FlowContext`` ids, the audit
``Stage`` it died in, the stable ``FailureCode``) to the columns of the two live
``quarantine.*`` tables. The ALLOWLIST decision is NOT here — ``orchestrate.py``
decides WHAT is quarantinable; this sink only knows HOW to hold it.

**Fail-loud (the deliberate asymmetry with ``sinks/audit.py``):** quarantine is the
held thing itself, the data path — every method RAISES on failure so the caller
falls back to nack (never ack-and-lose). The QUARANTINED *audit* emit stays
fire-and-forget and happens at the call site AFTER the hold succeeds.

Correlation (the D78 seam): ``data_ingress_event_id`` is the bronze row id (the
envelope's ``bronze_ref``, cross-checked by fetch); records carry ``trace_id`` +
``tenant_id`` always, ``mapping_version_id`` where known (post-lookup; NOT NULL on
the rows table — row-grain failures only exist post-lookup). ``failure_context``
carries column/check/reason grain ONLY — never cell values, never raw payload (the
raw row stays in GCS, located by ``gcs_uri`` + ``row_offset``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dis_audit import FailureCode, Stage
from dis_core.errors import QuarantineWriteError
from dis_core.timestamps import now_utc
from dis_quarantine import (
    PostgresQuarantineWriter,
    QuarantinedChunk,
    QuarantinedRow,
    failure_stage_for,
)
from streaming_consumer.envelope import IngressReadyEvent

if TYPE_CHECKING:
    from streaming_consumer.orchestrate import _FlowContext

# One gate failure as the orchestrator carries it: (column, row_index, check, reason).
GateFailure = tuple[str | None, int | None, str, str]


class ConsumerQuarantine:
    """Builds and holds this consumer's quarantined failures. Fail-loud."""

    def __init__(self, writer: PostgresQuarantineWriter) -> None:
        self._writer = writer

    async def hold_chunk_failure(
        self,
        event: IngressReadyEvent,
        ctx: _FlowContext,
        *,
        stage: Stage,
        failure_code: FailureCode,
        message: str,
        exception_class: str | None = None,
        failures: list[GateFailure] | None = None,
    ) -> None:
        """Hold the whole chunk in ``quarantined_chunks`` (status=NEW), or raise.

        ``dis_channel`` comes from the bronze row (post-fetch); a pre-fetch call is
        a caller bug — the allowlist guard excludes it — and raises so the message
        nacks rather than acks-and-loses.
        """
        if ctx.dis_channel is None:
            raise QuarantineWriteError(
                "hold_chunk_failure called pre-fetch (dis_channel unknown) — the allowlist "
                "guard must exclude pre-fetch failures (caller bug)",
                tenant_id=str(event.tenant_id),
                trace_id=str(event.trace_id),
                failure_code=str(failure_code),
            )
        context: dict[str, object] = {"failure_message": message[:2000]}
        if exception_class is not None:
            context["exception_class"] = exception_class
        if failures is not None:
            # Column/check/reason grain only (the dis-validation contract) — and the
            # row-less shape that routed this gate failure to the CHUNK table.
            context["failures"] = [
                {"column": column, "row_index": row_index, "check": check[:256], "reason": reason[:512]}
                for column, row_index, check, reason in failures
            ]
        record = QuarantinedChunk(
            tenant_id=event.tenant_id,
            store_id=event.store_id,
            data_ingress_event_id=ctx.bronze_id or event.bronze_ref,
            trace_id=event.trace_id,
            source_id=event.source_id,
            dis_channel=ctx.dis_channel,
            gcs_uri=event.gcs_uri,
            failure_stage=failure_stage_for(stage),
            failure_reason=str(failure_code),
            failure_context=context,
            mapping_version_id=ctx.mapping_version_id,
            row_count_in_chunk=ctx.row_count,
            quarantined_at=now_utc(),
        )
        await self._writer.hold_chunk(record)

    async def hold_row_failures(
        self,
        event: IngressReadyEvent,
        ctx: _FlowContext,
        *,
        stage: Stage,
        failures: list[GateFailure],
    ) -> int:
        """Hold the gate's failing rows in ``quarantined_rows`` (status=NEW), or raise.

        One record per DISTINCT failing row (a row with several failed checks is
        one held row); ``failure_context.failures`` aggregates that row's
        column/check/reason detail. Returns the number of rows held. Every failure
        must carry a ``row_index`` (the row-less shape routes to the chunk table at
        the call site) and the mapping must be loaded (``mapping_version_id`` is
        NOT NULL on the live rows table).
        """
        if ctx.dis_channel is None or ctx.mapping_version_id is None:
            raise QuarantineWriteError(
                "hold_row_failures called before fetch/lookup completed (caller bug): "
                f"dis_channel={ctx.dis_channel!r}, mapping_version_id={ctx.mapping_version_id!r}",
                tenant_id=str(event.tenant_id),
                trace_id=str(event.trace_id),
                failure_code=str(FailureCode.VALIDATION_ROW_FAILED),
            )
        by_row: dict[int, list[GateFailure]] = {}
        for failure in failures:
            row_index = failure[1]
            if row_index is None:
                raise QuarantineWriteError(
                    "hold_row_failures received a row-less failure — the call site routes "
                    "those to quarantined_chunks (caller bug)",
                    tenant_id=str(event.tenant_id),
                    trace_id=str(event.trace_id),
                    failure_code=str(FailureCode.VALIDATION_ROW_FAILED),
                )
            by_row.setdefault(row_index, []).append(failure)
        records = [
            QuarantinedRow(
                tenant_id=event.tenant_id,
                store_id=event.store_id,
                data_ingress_event_id=ctx.bronze_id or event.bronze_ref,
                trace_id=event.trace_id,
                source_id=event.source_id,
                dis_channel=ctx.dis_channel,
                gcs_uri=event.gcs_uri,
                row_offset=row_index,
                failure_stage=failure_stage_for(stage),
                failure_reason=str(FailureCode.VALIDATION_ROW_FAILED),
                failure_context={
                    "failures": [
                        {"column": column, "check": check[:256], "reason": reason[:512]}
                        for column, _, check, reason in row_failures
                    ]
                },
                mapping_version_id=ctx.mapping_version_id,
                quarantined_at=now_utc(),
            )
            for row_index, row_failures in sorted(by_row.items())
        ]
        await self._writer.hold_rows(records)
        return len(records)
