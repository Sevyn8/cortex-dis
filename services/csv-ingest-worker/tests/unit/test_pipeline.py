"""Pipeline orchestration: trust-boundary properties, ordering, and dispositions.

All DB touchpoints (rls_session / find_prior / insert_row / mark_published) are
monkeypatched with recorders so the ORDER of effects is assertable without a
stack: read/parse before any write, PII raise before the bronze write, bronze
INSERT before the publish, publish before the mark (AC2/4/5/6/7/8).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import csv_ingest_worker.pipeline as pipeline_module
from csv_ingest_worker.audit import WorkerAudit
from csv_ingest_worker.bronze import BronzeRow, PriorIngest
from csv_ingest_worker.envelope import parse_csv_received
from csv_ingest_worker.pipeline import IngestPipeline
from dis_audit import AuditEvent, Outcome, Stage
from dis_core.errors import (
    EventPathMismatchError,
    PiiBackendNotConfiguredError,
)

_CONTRACTS = Path(__file__).resolve().parents[3].parent / "contracts" / "pubsub"
_CSV_EXAMPLE = json.loads((_CONTRACTS / "csv.received.example.json").read_text())
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "csvs"
_WELL_FORMED = (_FIXTURES / "well_formed.csv").read_bytes()
_PII_CSV = b"sku,customer_email,qty\nA-1,a@example.com,5\n"  # real-heuristic header
_BUCKET = "ithina-bronze-raw"


def _event(**overrides: Any) -> Any:
    payload = dict(_CSV_EXAMPLE)
    payload.update(overrides)
    return parse_csv_received(json.dumps(payload).encode())


class _Recorder:
    """Ordered record of every side-effecting call across the fakes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def add(self, name: str, payload: Any = None) -> None:
        self.calls.append((name, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def first(self, name: str) -> Any:
        return next(payload for n, payload in self.calls if n == name)


class _FakeStorage:
    def __init__(self, recorder: _Recorder, data: bytes) -> None:
        self._recorder = recorder
        self._data = data

    def download_bytes(self, object_path: str) -> bytes:
        self._recorder.add("download", object_path)
        return self._data


class _FakePublisher:
    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder

    def publish(self, topic_name: str, data: bytes) -> str:
        self._recorder.add("publish", (topic_name, json.loads(data)))
        return "msg-1"


class _RecordingAuditWriter:
    def __init__(self, recorder: _Recorder, *, explode: bool = False) -> None:
        self._recorder = recorder
        self._explode = explode
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> bool:
        if self._explode:
            raise RuntimeError("injected audit failure")
        self.events.append(event)
        self._recorder.add("audit", (event.stage, event.outcome))
        return True


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    *,
    data: bytes = _WELL_FORMED,
    prior: PriorIngest | None = None,
    audit_explodes: bool = False,
) -> tuple[IngestPipeline, _RecordingAuditWriter]:
    """An IngestPipeline whose DB seams record instead of touching Postgres."""

    @asynccontextmanager
    async def fake_rls_session(engine: Any, tenant_id: Any) -> AsyncIterator[Any]:
        recorder.add("rls_session", str(tenant_id))
        yield object()

    async def fake_find_prior(conn: Any, **kwargs: Any) -> PriorIngest | None:
        recorder.add("find_prior", kwargs)
        return prior

    async def fake_insert_row(conn: Any, row: BronzeRow) -> None:
        recorder.add("insert", row)

    async def fake_mark_published(conn: Any, *, bronze_id: UUID, published_at: Any) -> None:
        recorder.add("mark_published", bronze_id)

    monkeypatch.setattr(pipeline_module, "rls_session", fake_rls_session)
    monkeypatch.setattr(pipeline_module, "find_prior", fake_find_prior)
    monkeypatch.setattr(pipeline_module, "insert_row", fake_insert_row)
    monkeypatch.setattr(pipeline_module, "mark_published", fake_mark_published)

    writer = _RecordingAuditWriter(recorder, explode=audit_explodes)
    pipeline = IngestPipeline(
        engine=object(),  # type: ignore[arg-type]  # never touched: rls_session is faked
        storage=_FakeStorage(recorder, data),
        publisher=_FakePublisher(recorder),
        audit=WorkerAudit(writer),
        bronze_bucket=_BUCKET,
    )
    return pipeline, writer


def _prior(**overrides: Any) -> PriorIngest:
    base: dict[str, Any] = {
        "bronze_id": UUID("019e93f0-57ca-7470-9899-ba6532ff15e1"),
        "trace_id": UUID("019e0000-0000-7000-8000-00000000aaaa"),  # != event trace
        "store_id": UUID(_CSV_EXAMPLE["store_id"]),
        "source_id": _CSV_EXAMPLE["source_id"],
        "gcs_uri": _CSV_EXAMPLE["gcs_uri"],
        "received_at": datetime(2026, 6, 5, 9, 0, tzinfo=UTC),
        "published_at": None,
        "processing_status": "RECEIVED",
    }
    base.update(overrides)
    return PriorIngest(**base)


# ---------------------------------------------------------------------------
# Happy path: ordering + trust-boundary properties (AC2, AC5, AC6).
# ---------------------------------------------------------------------------


async def test_happy_path_order_read_parse_before_write_then_conditional_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder)
    outcome = await pipeline.process(_event())

    names = recorder.names()
    # Read/parse strictly before any write; bronze INSERT before publish (D5);
    # publish before the mark (D59).
    assert names.index("download") < names.index("insert")
    assert names.index("find_prior") < names.index("insert")
    assert names.index("insert") < names.index("publish")
    assert names.index("publish") < names.index("mark_published")
    assert outcome.disposition == "ingested"
    # Slice 30b: every emitted stage row carries a non-negative duration (lap seam).
    assert writer.events
    assert all(e.duration_ms is not None and e.duration_ms >= 0 for e in writer.events)


async def test_emitted_trace_equals_event_trace_and_no_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC2: the worker reads trace_id off the event and mints none — proven by
    # making the minting function explode if anything calls it.
    import dis_core.trace_id as trace_id_module

    def _explode() -> Any:
        raise AssertionError("the worker must NEVER mint a trace_id (hard rule 4 / D54)")

    monkeypatch.setattr(trace_id_module, "new_trace_id", _explode)

    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder)
    outcome = await pipeline.process(_event())

    event_trace = _CSV_EXAMPLE["trace_id"]
    assert str(outcome.trace_id) == event_trace
    _, published = recorder.first("publish")
    assert published["trace_id"] == event_trace
    assert all(str(e.trace_id) == event_trace for e in writer.events)


async def test_rls_scope_is_the_event_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    # AC5: every session is opened under the EVENT's UUID tenant.
    recorder = _Recorder()
    pipeline, _ = _wire(monkeypatch, recorder)
    await pipeline.process(_event())
    scopes = {payload for name, payload in recorder.calls if name == "rls_session"}
    assert scopes == {_CSV_EXAMPLE["tenant_id"]}


async def test_bronze_row_is_metadata_only_with_event_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    pipeline, _ = _wire(monkeypatch, recorder)
    await pipeline.process(_event())
    row: BronzeRow = recorder.first("insert")
    assert row.tenant_id == UUID(_CSV_EXAMPLE["tenant_id"])
    assert row.store_id == UUID(_CSV_EXAMPLE["store_id"])  # single-store session
    assert row.trace_id == UUID(_CSV_EXAMPLE["trace_id"])
    assert row.source_payload_id == _CSV_EXAMPLE["upload_session_id"]
    assert row.template_id == UUID(_CSV_EXAMPLE["template_id"])  # replay lineage (Slice 8 / D71)
    assert row.processing_status == "RECEIVED"
    assert row.row_count == 3
    assert len(row.payload_sha256) == 64
    # Metadata only: the row dataclass has no payload/bytes field at all.
    assert not hasattr(row, "payload")


async def test_published_envelope_carries_bronze_ref_and_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    pipeline, _ = _wire(monkeypatch, recorder)
    outcome = await pipeline.process(_event())
    _, published = recorder.first("publish")
    assert published["bronze_ref"] == str(outcome.bronze_id)
    assert published["tenant_display_code"] == _CSV_EXAMPLE["tenant_display_code"]
    assert published["store_code"] == _CSV_EXAMPLE["store_code"]
    assert published["template_id"] == _CSV_EXAMPLE["template_id"]  # carried verbatim (D71)
    assert recorder.first("publish")[0] == "ingress.ready"


# ---------------------------------------------------------------------------
# Path cross-check (AC2): mismatch -> loud error, FAILURE audit, NO read/write.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "field"),
    [
        # Same shape, different tenant UUID in the path -> tenant_id mismatch.
        # The example tenant is buc-ees, so the mismatch uses zabka-group's UUID.
        (
            {
                "gcs_uri": _CSV_EXAMPLE["gcs_uri"].replace(
                    _CSV_EXAMPLE["tenant_id"], "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"
                )
            },
            "tenant_id",
        ),
        # Different source segment -> source_id mismatch.
        ({"source_id": "other_pos_v9"}, "source_id"),
        # Wrong bucket -> bucket mismatch (cross-checked against config).
        (
            {"gcs_uri": _CSV_EXAMPLE["gcs_uri"].replace("ithina-bronze-raw", "wrong-bucket")},
            "bucket",
        ),
    ],
)
async def test_path_mismatch_raises_loud_before_any_read_or_write(
    monkeypatch: pytest.MonkeyPatch, override: dict[str, Any], field: str
) -> None:
    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder)
    with pytest.raises(EventPathMismatchError) as exc_info:
        await pipeline.process(_event(**override))
    assert exc_info.value.field == field
    # Consistency check only — nothing was read, nothing was written.
    assert "download" not in recorder.names()
    assert "insert" not in recorder.names()
    assert "publish" not in recorder.names()
    # The FAILURE audit was emitted with the event's tenant/trace — and (Slice
    # 30b) the stable code plus the mismatch detail as event_data, not buried
    # in failure_message.
    [failure] = [e for e in writer.events if e.outcome is Outcome.FAILURE]
    assert failure.stage is Stage.RECEIVED
    assert failure.failure_code == "PATH_MISMATCH"
    assert failure.event_data is not None
    assert failure.event_data["field"] == field
    assert {"field", "event_value", "path_value"} <= failure.event_data.keys()
    assert failure.duration_ms is not None and failure.duration_ms >= 0


# ---------------------------------------------------------------------------
# PII gate (AC4): raise BEFORE the bronze write; injected backend passes.
# ---------------------------------------------------------------------------


async def test_pii_raise_precedes_bronze_write(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder, data=_PII_CSV)
    with pytest.raises(PiiBackendNotConfiguredError) as exc_info:
        await pipeline.process(_event())
    assert "customer_email" in exc_info.value.columns
    # The raise precedes ANY persistence: no bronze insert, no publish, no mark.
    assert "insert" not in recorder.names()
    assert "publish" not in recorder.names()
    assert "mark_published" not in recorder.names()
    # Slice 30b: stable code + the detected-column COUNT in event_data. The
    # bronze id stays NULL — correctly: the gate runs BEFORE the bronze write
    # (hard rule 2), so no bronze row exists at this emit (operator-confirmed).
    [pii_failure] = [e for e in writer.events if e.outcome is Outcome.FAILURE]
    assert pii_failure.stage is Stage.PII_TOKENIZED
    assert pii_failure.failure_code == "PII_BACKEND_NOT_CONFIGURED"
    assert pii_failure.data_ingress_event_id is None
    assert pii_failure.event_data is not None
    assert pii_failure.event_data["pii_columns_detected"] >= 1
    assert pii_failure.event_data["exception_class"] == "PiiBackendNotConfiguredError"


async def test_injected_backend_reaches_not_raise_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        def tokenize(self, value: str, *, tenant_id: str) -> str:
            return "tok"

    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder, data=_PII_CSV)
    pipeline.pii_backend = _Backend()
    outcome = await pipeline.process(_event())
    assert outcome.disposition == "ingested"
    pii_events = [e for e in writer.events if e.stage is Stage.PII_TOKENIZED]
    assert pii_events[0].event_data == {"pii_columns_detected": 1, "backend_configured": True}


# ---------------------------------------------------------------------------
# Preflight failure (AC3 / OQ4): FAILED bronze row + FAILURE audit, NO publish, ack.
# ---------------------------------------------------------------------------


async def test_preflight_failure_writes_failed_row_and_never_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder, data=b"\x00\x01\xff not a csv")
    outcome = await pipeline.process(_event())
    assert outcome.disposition == "preflight_failed"
    row: BronzeRow = recorder.first("insert")
    assert row.processing_status == "FAILED"
    assert row.row_count is None
    # Write-then-CONDITIONALLY-publish: the FAILED path publishes nothing.
    assert "publish" not in recorder.names()
    assert "mark_published" not in recorder.names()
    failures = [e for e in writer.events if e.outcome is Outcome.FAILURE]
    # Slice 30b: the stable vocabulary replaces the raw reason; the reason rides event_data.
    assert failures and failures[0].failure_code == "PREFLIGHT_NOT_CSV"
    assert failures[0].event_data is not None and failures[0].event_data["reason"] == "not_csv"
    assert failures[0].data_ingress_event_id == row.id


# ---------------------------------------------------------------------------
# Idempotency (AC7 / D59): no-op vs resume, both ways.
# ---------------------------------------------------------------------------


async def test_duplicate_with_published_prior_is_full_noop_returning_prior_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _prior(published_at=datetime(2026, 6, 5, 9, 1, tzinfo=UTC), processing_status="PUBLISHED")
    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder, prior=prior)
    outcome = await pipeline.process(_event())
    assert outcome.disposition == "duplicate_noop"
    assert outcome.trace_id == prior.trace_id  # the PRIOR trace, not the event's
    assert "insert" not in recorder.names()  # no second bronze row
    assert "publish" not in recorder.names()  # no second publish
    # FLIPPED by Slice 30c (the D42 revision): the kind is the OUTCOME and the
    # prior trace is a COLUMN — no longer SKIPPED + event_data JSONB keys.
    [noop] = [e for e in writer.events if e.outcome is Outcome.DUPLICATE_NOOP]
    assert noop.prior_trace_id == prior.trace_id  # the column
    assert noop.event_data is not None
    assert noop.event_data["prior_status"] == "PUBLISHED"
    assert "duplicate" not in noop.event_data  # the old JSONB keys are gone
    assert "prior_trace_id" not in noop.event_data


async def test_duplicate_with_failed_prior_is_noop_without_republish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A FAILED prior is terminal: redelivery of the same bad bytes must not
    # re-preflight, re-write, or publish.
    prior = _prior(processing_status="FAILED")
    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder, prior=prior)
    outcome = await pipeline.process(_event())
    assert outcome.disposition == "duplicate_noop"
    assert "insert" not in recorder.names()
    assert "publish" not in recorder.names()
    # Slice 30c: this path too sets the DUPLICATE_NOOP outcome + the column.
    [noop] = [e for e in writer.events if e.outcome is Outcome.DUPLICATE_NOOP]
    assert noop.prior_trace_id == prior.trace_id


async def test_duplicate_with_unpublished_prior_resumes_publish_and_marks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # D59 resume-and-mark: complete the lost publish under the PRIOR trace_id,
    # stamp published_at, write no second bronze row.
    prior = _prior()  # RECEIVED, published_at NULL
    recorder = _Recorder()
    pipeline, writer = _wire(monkeypatch, recorder, prior=prior)
    outcome = await pipeline.process(_event())
    assert outcome.disposition == "duplicate_resumed"
    assert outcome.trace_id == prior.trace_id
    assert "insert" not in recorder.names()  # no second bronze row
    topic, published = recorder.first("publish")
    assert topic == "ingress.ready"
    assert published["trace_id"] == str(prior.trace_id)
    assert published["bronze_ref"] == str(prior.bronze_id)
    assert recorder.first("mark_published") == prior.bronze_id
    names = recorder.names()
    assert names.index("publish") < names.index("mark_published")
    # Slice 30c: the resume is a retry-completion — legible as RETRIED.
    [resumed] = [e for e in writer.events if e.stage is Stage.INGRESS_PUBLISHED]
    assert resumed.outcome is Outcome.RETRIED
    assert resumed.event_data is not None and resumed.event_data["resumed"] is True


async def test_resume_against_pre_slice8_null_template_prior_cannot_wedge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The operator-flagged edge: an unpublished prior bronze row from BEFORE the
    # 0006 migration has template_id NULL. The resume publish must not read the
    # template off bronze (PriorIngest deliberately carries none — asserted here),
    # must publish with the INCOMING event's template_id, and must conclude in a
    # normal acked disposition — never an error that NACK/redelivers into a
    # poison-message loop.
    prior = _prior()  # RECEIVED, unpublished; the fixture has NO template field
    assert not hasattr(prior, "template_id")
    recorder = _Recorder()
    pipeline, _ = _wire(monkeypatch, recorder, prior=prior)
    outcome = await pipeline.process(_event())  # completes; the subscriber acks this
    assert outcome.disposition == "duplicate_resumed"
    _, published = recorder.first("publish")
    assert published["template_id"] == _CSV_EXAMPLE["template_id"]  # from the event
    assert recorder.first("mark_published") == prior.bronze_id  # loop-breaker stamped


# ---------------------------------------------------------------------------
# Audit fire-and-forget (AC8): an exploding audit backend never blocks the path.
# ---------------------------------------------------------------------------


async def test_injected_audit_failure_does_not_block_the_data_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    pipeline, _ = _wire(monkeypatch, recorder, audit_explodes=True)
    outcome = await pipeline.process(_event())
    assert outcome.disposition == "ingested"  # data path completed
    names = recorder.names()
    assert names.index("insert") < names.index("publish") < names.index("mark_published")
