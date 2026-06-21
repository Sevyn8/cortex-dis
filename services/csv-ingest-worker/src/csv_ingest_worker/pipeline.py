"""The per-event ingest pipeline: trust the event, preflight, gate, write, publish.

Stage order (one concern per function, code-quality rule 7):

1. path cross-check  — split_object_uri + parse_object_path vs the event (D53);
                       a mismatch is a malformed PRODUCER (loud error), never a
                       re-resolution — the event stays the trust boundary (D54).
2. read + hash       — download via dis-storage; sha256 (the content-hash key part).
3. idempotency       — dedup lookup under rls_session. PUBLISHED or FAILED prior →
                       full no-op returning the PRIOR trace_id. Unpublished RECEIVED
                       prior → resume-and-mark (D59): complete the lost publish, no
                       second bronze row.
4. preflight         — DuckDB structural sniff. Failure → bronze FAILED row + audit,
                       NO publish (write-then-CONDITIONALLY-publish), terminal.
5. PII gate          — dis-pii fail-loud over the sniffed header, BEFORE the
                       RECEIVED-path bronze write (hard rule 2).
6. bronze write      — one metadata-only row via rls_session (hard rules 1 & 12).
7. publish + mark    — frozen ingress.ready AFTER bronze lands (D5), then stamp
                       published_at/PUBLISHED. A crash between 6 and 7 is healed by
                       the step-3 resume branch on redelivery.

The worker READS identity and trace_id off the event and NEVER mints a trace_id or
resolves identity (D54, hard rule 4): nothing here imports dis_core.identity or
calls a trace-id generator for the trace (the bronze row id is minted via
dis-core ``new_uuid7`` — an id, not a trace).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from csv_ingest_worker.audit import WorkerAudit
from csv_ingest_worker.bronze import (
    BronzeRow,
    PriorIngest,
    find_prior,
    insert_row,
    mark_published,
)
from csv_ingest_worker.config import INGRESS_READY_TOPIC, SERVICE_NAME
from csv_ingest_worker.envelope import CsvReceivedEvent
from csv_ingest_worker.pii_gate import gate_csv_headers
from csv_ingest_worker.preflight import PreflightResult, run_preflight
from csv_ingest_worker.publisher import Publisher, build_ingress_ready
from dis_audit import FailureCode, Outcome, Stage, failure_code_for
from dis_core.errors import EventPathMismatchError, PiiBackendNotConfiguredError, PreflightFailedError
from dis_core.ids import new_uuid7
from dis_core.logging import get_logger
from dis_core.timestamps import now_utc
from dis_pii import PiiBackend
from dis_rls import rls_session
from dis_storage import parse_object_path, split_object_uri

_log = get_logger(SERVICE_NAME)

Disposition = Literal["ingested", "duplicate_noop", "duplicate_resumed", "preflight_failed"]

# The DuckDB preflight's closed reason set -> the stable vocabulary (Slice 30b).
_PREFLIGHT_CODES: dict[str, FailureCode] = {
    "not_csv": FailureCode.PREFLIGHT_NOT_CSV,
    "no_columns": FailureCode.PREFLIGHT_NO_COLUMNS,
    "no_header": FailureCode.PREFLIGHT_NO_HEADER,
    "no_data_rows": FailureCode.PREFLIGHT_NO_DATA_ROWS,
}


@dataclass
class _Lap:
    """The per-stage duration seam (Slice 30b): stages run sequentially, so
    elapsed-since-the-previous-audit-point IS the stage span at audit grain."""

    _mark: float = field(default_factory=time.monotonic)

    def lap(self) -> int:
        now = time.monotonic()
        elapsed_ms = int((now - self._mark) * 1000)
        self._mark = now
        return max(elapsed_ms, 0)


class ObjectStore(Protocol):
    """The read seam over dis-storage's client (tests inject a fake)."""

    def download_bytes(self, object_path: str) -> bytes: ...


@dataclass(frozen=True)
class IngestOutcome:
    """What one csv.received event produced (the subscriber acks on any of these)."""

    disposition: Disposition
    trace_id: UUID  # ALWAYS a read trace_id: the event's, or the prior ingest's
    bronze_id: UUID | None


@dataclass
class IngestPipeline:
    """One worker process's wired dependencies (caller-owned, mirror-sync pattern)."""

    engine: AsyncEngine
    storage: ObjectStore
    publisher: Publisher
    audit: WorkerAudit
    bronze_bucket: str
    pii_backend: PiiBackend | None = None

    async def process(self, event: CsvReceivedEvent) -> IngestOutcome:
        """Run one event through the pipeline. Raises CsvIngestError-family on
        terminal contract/content failures (the subscriber acks those)."""
        log = _log.bind(stage="pipeline", tenant_id=str(event.tenant_id), trace_id=str(event.trace_id))
        lap = _Lap()

        # 1. Path cross-check (consistency check, not re-resolution — D54).
        object_key = await self._cross_check_path(event, lap)

        # 2. Read + hash (read-only; before any write).
        data = self.storage.download_bytes(object_key)
        payload_sha256 = hashlib.sha256(data).hexdigest()

        # 3. Idempotency: the dedup lookup, before any compute or write.
        async with rls_session(self.engine, event.tenant_id) as conn:
            prior = await find_prior(
                conn,
                upload_session_id=event.upload_session_id,
                payload_sha256=payload_sha256,
                tenant_id=str(event.tenant_id),
                trace_id=str(event.trace_id),
            )
        if prior is not None:
            return await self._handle_duplicate(event, prior, data, lap)

        # 4. Structural preflight (D13/D16). Failure → FAILED bronze row, no publish.
        try:
            preflight = run_preflight(data, tenant_id=str(event.tenant_id), trace_id=str(event.trace_id))
        except PreflightFailedError as exc:
            return await self._handle_preflight_failure(event, payload_sha256, len(data), exc, lap)

        await self.audit.emit(
            stage=Stage.RECEIVED,
            outcome=Outcome.SUCCESS,
            tenant_id=event.tenant_id,
            trace_id=event.trace_id,
            row_count=preflight.row_count,
            duration_ms=lap.lap(),
            event_data={
                "preflight": {
                    "columns": len(preflight.columns),
                    "row_count": preflight.row_count,
                    "size_bytes": preflight.size_bytes,
                },
                "upload_session_id": event.upload_session_id,
            },
        )

        # 5. PII gate — BEFORE the bronze write (hard rule 2). Fail-loud: a detected
        #    column with no backend raises (and v1.0 has no backend, D40).
        detected = await self._gate_pii(event, preflight, lap)

        # 6. Bronze write (metadata only) via dis-rls under the EVENT's tenant.
        bronze_id = new_uuid7()
        received_at = now_utc()
        row = BronzeRow(
            id=bronze_id,
            tenant_id=event.tenant_id,
            store_id=event.store_id,
            source_id=event.source_id,
            trace_id=event.trace_id,
            gcs_uri=event.gcs_uri,
            payload_size_bytes=preflight.size_bytes,
            payload_sha256=payload_sha256,
            row_count=preflight.row_count,
            source_payload_id=event.upload_session_id,
            template_id=event.template_id,
            received_at=received_at,
            processing_status="RECEIVED",
        )
        async with rls_session(self.engine, event.tenant_id) as conn:
            await insert_row(conn, row)
        await self.audit.emit(
            stage=Stage.BRONZE_WRITTEN,
            outcome=Outcome.SUCCESS,
            tenant_id=event.tenant_id,
            trace_id=event.trace_id,
            bronze_id=bronze_id,
            row_count=preflight.row_count,
            duration_ms=lap.lap(),
            event_data={"pii_columns_detected": len(detected)},
        )

        # 7. Publish AFTER bronze lands (D5), then stamp the publish (D59).
        envelope = build_ingress_ready(
            event,
            trace_id=event.trace_id,
            bronze_ref=bronze_id,
            received_at=received_at,
            delimiter=preflight.delimiter,
        )
        self.publisher.publish(INGRESS_READY_TOPIC, envelope.to_bytes())
        async with rls_session(self.engine, event.tenant_id) as conn:
            await mark_published(conn, bronze_id=bronze_id, published_at=now_utc())
        await self.audit.emit(
            stage=Stage.INGRESS_PUBLISHED,
            outcome=Outcome.SUCCESS,
            tenant_id=event.tenant_id,
            trace_id=event.trace_id,
            bronze_id=bronze_id,
            duration_ms=lap.lap(),
            event_data={"topic": INGRESS_READY_TOPIC},
        )
        log.info("ingested")
        return IngestOutcome(disposition="ingested", trace_id=event.trace_id, bronze_id=bronze_id)

    # -- stage helpers (one concern each) ---------------------------------------

    async def _cross_check_path(self, event: CsvReceivedEvent, lap: _Lap) -> str:
        """Split + parse the event's gcs_uri and require it to agree with the event."""
        bucket, object_key = split_object_uri(event.gcs_uri)
        checks: list[tuple[str, str, str]] = [("bucket", bucket, self.bronze_bucket)]
        parsed = parse_object_path(object_key)
        checks.extend(
            [
                ("tenant_id", str(parsed.tenant_id), str(event.tenant_id)),
                ("source_id", parsed.source_id, event.source_id),
                ("trace_id", parsed.trace_id, str(event.trace_id)),
                ("ext", parsed.ext, "csv"),
            ]
        )
        for field_name, path_value, event_value in checks:
            if path_value != event_value:
                error = EventPathMismatchError(
                    f"csv.received gcs_uri disagrees with the event on {field_name!r} "
                    "(malformed producer; the event is the trust boundary, D54)",
                    field=field_name,
                    event_value=event_value,
                    path_value=path_value,
                    tenant_id=str(event.tenant_id),
                    trace_id=str(event.trace_id),
                )
                await self.audit.emit(
                    stage=Stage.RECEIVED,
                    outcome=Outcome.FAILURE,
                    tenant_id=event.tenant_id,
                    trace_id=event.trace_id,
                    duration_ms=lap.lap(),
                    failure_code=FailureCode.PATH_MISMATCH,
                    failure_message=str(error),
                    # Identifiers only, never payload (Slice 30b: the mismatch
                    # detail rides event_data instead of being buried in fmsg).
                    event_data={
                        "field": field_name,
                        "event_value": event_value,
                        "path_value": path_value,
                    },
                )
                raise error
        return object_key

    async def _handle_duplicate(
        self, event: CsvReceivedEvent, prior: PriorIngest, data: bytes, lap: _Lap
    ) -> IngestOutcome:
        """Redelivery semantics (D59): full no-op, or resume the lost publish.

        ``data`` (the downloaded bytes, in hand from the pre-dedup read) lets the
        resume branch re-derive the delimiter via a fresh ``run_preflight`` so the
        republished ``ingress.ready`` carries the correct separator (Slice 16f) — the
        worker sets it on EVERY publish path, never defaults the resume to comma. The
        re-sniff is deterministic over the same bytes; the prior row is RECEIVED, so
        preflight already passed on them and passes again.
        """
        log = _log.bind(stage="idempotency", tenant_id=str(event.tenant_id), trace_id=str(event.trace_id))
        if prior.processing_status == "FAILED" or prior.is_published:
            # Same content + session + tenant already concluded → no second bronze
            # row, no second publish; return the PRIOR trace_id. Slice 30c (the
            # D42 revision): the duplicate kind is the OUTCOME and the prior
            # trace is a COLUMN — queryable, not event_data keys.
            await self.audit.emit(
                stage=Stage.RECEIVED,
                outcome=Outcome.DUPLICATE_NOOP,
                tenant_id=event.tenant_id,
                trace_id=event.trace_id,
                prior_trace_id=prior.trace_id,
                bronze_id=prior.bronze_id,
                duration_ms=lap.lap(),
                event_data={"prior_status": prior.processing_status},
            )
            log.info("duplicate within dedup window; no-op")
            return IngestOutcome(
                disposition="duplicate_noop", trace_id=prior.trace_id, bronze_id=prior.bronze_id
            )

        # Unpublished RECEIVED prior: bronze landed, the publish was lost. Complete
        # it under the PRIOR trace_id and mark it (no second bronze row). A rare
        # duplicate publish is tolerated (Pub/Sub is at-least-once; Slice 10 dedups).
        # Re-derive the delimiter from the same bytes so the resumed publish carries
        # the correct separator (Slice 16f); the prior RECEIVED row means preflight
        # already passed on these bytes, so this re-sniff does not newly fail.
        preflight = run_preflight(data, tenant_id=str(event.tenant_id), trace_id=str(event.trace_id))
        envelope = build_ingress_ready(
            event,
            trace_id=prior.trace_id,
            bronze_ref=prior.bronze_id,
            received_at=prior.received_at,
            delimiter=preflight.delimiter,
        )
        self.publisher.publish(INGRESS_READY_TOPIC, envelope.to_bytes())
        async with rls_session(self.engine, event.tenant_id) as conn:
            await mark_published(conn, bronze_id=prior.bronze_id, published_at=now_utc())
        await self.audit.emit(
            stage=Stage.INGRESS_PUBLISHED,
            # Slice 30c: the resume IS a retry-completion (the lost publish,
            # completed on redelivery) — RETRIED makes it legible as one.
            outcome=Outcome.RETRIED,
            tenant_id=event.tenant_id,
            trace_id=prior.trace_id,
            bronze_id=prior.bronze_id,
            duration_ms=lap.lap(),
            event_data={"resumed": True, "topic": INGRESS_READY_TOPIC},
        )
        log.info("duplicate with unpublished prior; publish resumed and marked")
        return IngestOutcome(
            disposition="duplicate_resumed", trace_id=prior.trace_id, bronze_id=prior.bronze_id
        )

    async def _handle_preflight_failure(
        self,
        event: CsvReceivedEvent,
        payload_sha256: str,
        size_bytes: int,
        error: PreflightFailedError,
        lap: _Lap,
    ) -> IngestOutcome:
        """Preflight failure: bronze FAILED row + audit, NO publish, terminal (D13).

        The FAILED row persists nothing column-derived (metadata only), so there is
        nothing for the PII gate to gate on this path; the gate guards the
        RECEIVED-path write. The row makes the failure durable/ops-queryable and
        lets the dedup absorb a redelivery of the same bad bytes.
        """
        bronze_id = new_uuid7()
        row = BronzeRow(
            id=bronze_id,
            tenant_id=event.tenant_id,
            store_id=event.store_id,
            source_id=event.source_id,
            trace_id=event.trace_id,
            gcs_uri=event.gcs_uri,
            payload_size_bytes=size_bytes,
            payload_sha256=payload_sha256,
            row_count=None,  # nothing parsed
            source_payload_id=event.upload_session_id,
            template_id=event.template_id,
            received_at=now_utc(),
            processing_status="FAILED",
        )
        async with rls_session(self.engine, event.tenant_id) as conn:
            await insert_row(conn, row)
        await self.audit.emit(
            stage=Stage.RECEIVED,
            outcome=Outcome.FAILURE,
            tenant_id=event.tenant_id,
            trace_id=event.trace_id,
            bronze_id=bronze_id,
            duration_ms=lap.lap(),
            # The stable vocabulary (Slice 30b); the raw reason rides event_data.
            failure_code=_PREFLIGHT_CODES.get(error.reason or "", FailureCode.INFRA_FAILURE),
            failure_message=str(error),
            event_data={"preflight_failed": True, "reason": error.reason, "detail": error.detail},
        )
        _log.bind(stage="preflight", tenant_id=str(event.tenant_id), trace_id=str(event.trace_id)).error(
            "structural preflight failed; FAILED bronze row written, no publish"
        )
        return IngestOutcome(disposition="preflight_failed", trace_id=event.trace_id, bronze_id=bronze_id)

    async def _gate_pii(
        self, event: CsvReceivedEvent, preflight: PreflightResult, lap: _Lap
    ) -> frozenset[str]:
        """The fail-loud gate over the sniffed header; FAILURE audit before re-raise.

        The FAILURE row's ``data_ingress_event_id`` is correctly NULL: the gate
        runs BEFORE the bronze write (hard rule 2 — PII never lands), so no
        bronze row exists at this point (Slice 30b register note: the detected
        COUNT rides ``event_data``; names/values never do).
        """
        try:
            detected = gate_csv_headers(
                preflight.columns,
                tenant_id=str(event.tenant_id),
                trace_id=str(event.trace_id),
                backend=self.pii_backend,
            )
        except Exception as exc:
            event_data: dict[str, object] = {"exception_class": type(exc).__name__}
            if isinstance(exc, PiiBackendNotConfiguredError):
                event_data["pii_columns_detected"] = len(exc.columns)
            await self.audit.emit(
                stage=Stage.PII_TOKENIZED,
                outcome=Outcome.FAILURE,
                tenant_id=event.tenant_id,
                trace_id=event.trace_id,
                duration_ms=lap.lap(),
                failure_code=failure_code_for(exc),
                failure_message=str(exc),
                event_data=event_data,
            )
            raise  # fail-loud: PII never lands silently (hard rule 2)
        await self.audit.emit(
            stage=Stage.PII_TOKENIZED,
            outcome=Outcome.SUCCESS,
            tenant_id=event.tenant_id,
            trace_id=event.trace_id,
            duration_ms=lap.lap(),
            event_data={
                "pii_columns_detected": len(detected),
                "backend_configured": self.pii_backend is not None,
            },
        )
        return detected
