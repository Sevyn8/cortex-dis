"""WorkerAudit: fire-and-forget (hard rule 11) — failures logged, never raised,
never blocking; events carry tenant_id, trace_id, and the load-bearing id (AC8)."""

from __future__ import annotations

import logging
from uuid import UUID

import pytest

from csv_ingest_worker.audit import WorkerAudit
from dis_audit import AuditEvent, EventScope, Outcome, Stage

_TENANT = UUID("019e5e3c-b5d3-705f-9002-2451c4ca2626")  # buc-ees
_TRACE = UUID("019e8d88-4e76-7911-bb77-d8fcba1808a6")
_BRONZE = UUID("019e93f0-57ca-7470-9899-ba6532ff15e1")


class _RecordingWriter:
    def __init__(self, *, result: bool = True) -> None:
        self.result = result
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> bool:
        self.events.append(event)
        return self.result


class _RaisingWriter:
    async def write(self, event: AuditEvent) -> bool:
        raise RuntimeError("injected audit backend explosion")


async def test_emit_builds_event_with_context_and_load_bearing_id() -> None:
    writer = _RecordingWriter()
    audit = WorkerAudit(writer)
    await audit.emit(
        stage=Stage.BRONZE_WRITTEN,
        outcome=Outcome.SUCCESS,
        tenant_id=_TENANT,
        trace_id=_TRACE,
        bronze_id=_BRONZE,
        row_count=3,
        event_data={"pii_columns_detected": 0},
    )
    [event] = writer.events
    assert event.tenant_id == _TENANT  # D43: every event carries a known tenant
    assert event.trace_id == _TRACE  # read trace, propagated
    assert event.data_ingress_event_id == _BRONZE  # the load-bearing id
    assert event.service_name == "csv-ingest-worker"
    assert event.stage is Stage.BRONZE_WRITTEN
    assert event.event_scope is EventScope.INGRESS_EVENT
    assert event.outcome is Outcome.SUCCESS
    assert event.row_count == 3
    assert event.event_data == {"pii_columns_detected": 0}


async def test_writer_raise_is_swallowed_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The ONE sanctioned swallow (code-quality rule 6 / hard rule 11): a raising
    # backend must not propagate to the data path.
    audit = WorkerAudit(_RaisingWriter())
    with caplog.at_level(logging.ERROR):
        await audit.emit(
            stage=Stage.RECEIVED,
            outcome=Outcome.FAILURE,
            tenant_id=_TENANT,
            trace_id=_TRACE,
            failure_code="PATH_MISMATCH",
        )  # no raise == the data path continues
    assert any("audit emission raised" in r.message for r in caplog.records)


async def test_writer_false_is_logged_as_error_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = _RecordingWriter(result=False)
    audit = WorkerAudit(writer)
    with caplog.at_level(logging.ERROR):
        await audit.emit(
            stage=Stage.INGRESS_PUBLISHED,
            outcome=Outcome.SUCCESS,
            tenant_id=_TENANT,
            trace_id=_TRACE,
            bronze_id=_BRONZE,
        )
    assert any("audit write reported failure" in r.message for r in caplog.records)


async def test_duplicate_emission_is_tolerated() -> None:
    # D44: duplicates tolerated — emitting the same stage event twice is fine.
    writer = _RecordingWriter()
    audit = WorkerAudit(writer)
    for _ in range(2):
        await audit.emit(
            stage=Stage.RECEIVED,
            outcome=Outcome.SKIPPED,
            tenant_id=_TENANT,
            trace_id=_TRACE,
            event_data={"duplicate": True, "prior_trace_id": str(_TRACE)},
        )
    assert len(writer.events) == 2
