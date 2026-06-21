"""Per-event stage orchestration: fetch → load → gate → map → gate → write.

(Named ``orchestrate.py``: a top-level ``pipeline.py`` cannot coexist with the
``pipeline/`` package; repo-structure reserves no orchestrator name.)

Stage order (one concern per function, code-quality rule 7):

1. fetch            — cross-check + bronze read + GCS download (D5, D53, rule 9)
2. mapping load     — per-lookup ACTIVE mapping (D6); routing (sale-vs-change)
3. store read       — sale path only: tax_treatment denormalization source
4. pre-validation   — source-shape suite (D13/D18); failure → disposition
5. engine           — the pure four sub-stages; ANY cell failure → disposition
                      (no B2 threshold, no per-row split — Slice 11)
6. post-validation  — canonical-shape suite + drift guard; failure → disposition
7. dual-write       — mapping_version_id stamp (D22) + atomic hot+event (D30)
8. duplicate audit  — D42 ROW events for dedup-key hits (read-time-dedup posture)

**Failure disposition (audit-and-nack, with the Slice 11a quarantine carve-out):**
a failing chunk gets a FAILURE audit (fire-and-forget) and — on a NARROW ALLOWLIST
of failures KNOWN to be deterministic (retrying genuinely cannot help) — is held in
the ``quarantine.*`` store, a QUARANTINED audit is emitted, and the chunk returns
the ``quarantined`` disposition the subscriber ACKS: the storm-class redeliver loop
is broken at its source. Everything else keeps audit-and-nack: a ``failed_*``
disposition or a propagated exception NACKS — never silently dropped (bronze
remains the recoverable source, D5), never partially written (validation precedes
the write; write failures roll the batch back, D30). The governing principle: a
failure waiting for a dependency to arrive (catalogue onboarding → the D63
``HOT_POSITION_MISSING`` miss; mirror-sync lag → the store-miss contract
violation) is NOT deterministic in the relevant sense — retry is its designed
recovery — so it stays on the nack path. A QUARANTINE-WRITE failure also nacks
(fail-loud, never ack-and-lose); the Pub/Sub dead-letter policy backstops.

The consumer reads ``trace_id`` and identity off the event and mints neither
(hard rule 4); any IDs it does mint (event-row ids) come from dis-core ``ids``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_audit import EventScope, FailureCode, Outcome, Stage, failure_code_for
from dis_canonical import StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_core.logging import get_logger
from dis_rls import rls_session
from streaming_consumer.config import BATCH_SIZE_ROW_PAIRS, SERVICE_NAME
from streaming_consumer.envelope import IngressReadyEvent
from streaming_consumer.pipeline.fetch import (
    BronzeMeta,
    FetchedChunk,
    ObjectStore,
    StoreFacts,
    fetch_chunk,
    read_store_facts,
)
from streaming_consumer.pipeline.mapping import (
    LoadedMapping,
    apply_loaded_enrichment,
    apply_loaded_mapping,
    load_active_mapping,
)
from streaming_consumer.pipeline.normalize import build_event_rows
from streaming_consumer.pipeline.validate_post import run_post_validation
from streaming_consumer.pipeline.validate_pre import run_pre_validation
from streaming_consumer.sinks.audit import ConsumerAudit
from streaming_consumer.sinks.canonical import WriteReport, write_catalogue_chunk, write_chunk
from streaming_consumer.sinks.quarantine import ConsumerQuarantine, GateFailure

_log = get_logger(SERVICE_NAME)

Disposition = Literal[
    "written",
    "quarantined",
    "failed_pre_validation",
    "failed_mapping",
    "failed_post_validation",
]


@dataclass(frozen=True)
class ConsumeOutcome:
    """What one ingress.ready event produced.

    The subscriber acks 'written' and 'quarantined' (the held chunk must leave the
    queue — that ack IS the Slice 11a storm fix); every other disposition nacks.
    """

    disposition: Disposition
    report: WriteReport | None = None


# The gate-summary failure codes per validating stage (Slice 30b stable vocabulary).
_GATE_FAILURE_CODES: dict[Stage, FailureCode] = {
    Stage.PRE_MAPPING_VALIDATED: FailureCode.PRE_VALIDATION_FAILED,
    Stage.MAPPING_EXECUTED: FailureCode.MAPPING_EXECUTION_FAILED,
    Stage.POST_MAPPING_VALIDATED: FailureCode.POST_VALIDATION_FAILED,
}

# Slice 11a storm stopper: the NARROW allowlist of exception-path failures KNOWN to
# fail identically on every retry (config-deterministic: only a config change + a
# future replay recovers them), so hold-and-ACK is correct. Deliberately ABSENT —
# the governing principle (a failure waiting for a dependency self-heals via
# redelivery; retry is its designed recovery, so it keeps nacking until replay
# exists):
#   - FailureCode.HOT_POSITION_MISSING — the D63 miss; the event rows are already
#     committed and a redelivery completes the hot upsert once the catalogue/
#     position onboards (service CLAUDE.md).
#   - the CONTRACT_VIOLATION store-miss case — excluded by field in
#     _quarantinable() below; mirror-sync lag heals it (the same shape).
#   - FailureCode.INFRA_FAILURE and every unmapped/transient code.
# Gate failures (VALIDATION_ROW_FAILED detail) route separately in
# _quarantine_gate_failure (rows where the index is known; chunk where row-less).
_CHUNK_QUARANTINE_CODES: frozenset[FailureCode] = frozenset(
    {
        FailureCode.MAPPING_CONFIG_INVALID,
        FailureCode.SUITE_REF_UNSUPPORTED,
        FailureCode.CONTRACT_VIOLATION,
    }
)


def _quarantinable(exc: Exception, code: FailureCode, ctx: _FlowContext) -> bool:
    """Is this exception-path failure on the 11a allowlist AND fully describable?

    Three gates, all of which must pass:
    - the stable code is in the narrow allowlist;
    - the known-columns guard: ``dis_channel`` only exists post-fetch (the bronze
      row carries it), and ``quarantined_chunks.dis_channel`` is NOT NULL — a
      pre-fetch failure (e.g. the bronze-absent contract violation) cannot be
      held, so it keeps today's nack;
    - the store-miss carve-out: ``EventContractError(field='store_id')`` (the
      sale-path tax read against an unmirrored store) self-heals on mirror-sync
      lag — the governing principle keeps it on the nack path.
    """
    if code not in _CHUNK_QUARANTINE_CODES:
        return False
    if ctx.dis_channel is None:
        return False
    return not (code is FailureCode.CONTRACT_VIOLATION and getattr(exc, "field", None) == "store_id")


@dataclass
class _FlowContext:
    """What the catch-all needs to know about a partially-processed event.

    ``_process`` records the correlation ids as they become known (post-fetch →
    ``bronze_id``, ``dis_channel``, ``row_count``; post-lookup →
    ``mapping_version_id``) so a FAILURE audit never buries an id it knows in
    ``failure_message`` (the Slice 30b failure-audit shape — the seam the Slice 11a
    quarantine records consume: ``dis_channel``/``row_count`` exist exactly so a
    held chunk satisfies the quarantine table's NOT NULLs, and the allowlist guard
    keys on ``dis_channel is None`` to refuse pre-fetch holds). ``lap()`` is the
    per-stage duration seam: stages run strictly sequentially, so elapsed-since-
    the-previous-audit-point IS the stage span (at audit grain — small
    non-emitting work like the tax read rides the following stage's lap; not
    micro-profiled).
    """

    bronze_id: UUID | None = None
    mapping_version_id: int | None = None
    dis_channel: str | None = None
    row_count: int | None = None
    _mark: float = field(default_factory=time.monotonic)

    def note_bronze(self, bronze: BronzeMeta) -> None:
        """Record the bronze identity the moment the row is read (mid-fetch).

        The fetch stage can still fail AFTER this point (download/parse); such a
        failure is genuinely post-fetch and must not lose ``dis_channel`` — the
        ``_quarantinable`` known-columns guard keys on it, and losing it would
        misclassify a deterministic post-fetch failure as pre-fetch (nack-forever,
        the storm class Slice 11a holds).
        """
        self.bronze_id = bronze.bronze_id
        self.dis_channel = bronze.dis_channel

    def lap(self) -> int:
        now = time.monotonic()
        elapsed_ms = int((now - self._mark) * 1000)
        self._mark = now
        return max(elapsed_ms, 0)


@dataclass
class ConsumerPipeline:
    """One consumer process's wired dependencies (caller-owned, 9b pattern)."""

    engine: AsyncEngine
    storage: ObjectStore
    audit: ConsumerAudit
    quarantine: ConsumerQuarantine
    bronze_bucket: str
    batch_size: int = BATCH_SIZE_ROW_PAIRS

    async def process(self, event: IngressReadyEvent) -> ConsumeOutcome:
        """Run one event through the pipeline.

        On an exception, emits a FAILURE audit where identity is known, then —
        Slice 11a — either holds the chunk in quarantine and returns the
        ``quarantined`` disposition the subscriber ACKS (the narrow deterministic
        allowlist: the storm fix), or re-raises so the subscriber nacks
        (audit-and-nack, today's behavior for everything else). The ordering on
        the quarantine path is load-bearing: quarantine-write-loud → QUARANTINED
        audit-emit-forget → ack; a failed HOLD falls back to the nack (never
        ack-and-lose), a failed AUDIT emit never blocks the ack of a
        successfully-held chunk (hard rule 11). The FAILURE audit carries every
        correlation id known at the point of failure (``_FlowContext``) and a
        stable ``FailureCode`` — never a bare exception class name (Slice 30b
        failure-audit shape).
        """
        stage = Stage.RECEIVED
        ctx = _FlowContext()
        try:
            return await self._process(event, ctx)
        except Exception as exc:
            tagged = getattr(exc, "_dis_stage", stage)
            failed_stage = tagged if isinstance(tagged, Stage) else Stage.RECEIVED
            failure_code = failure_code_for(exc)
            await self.audit.emit(
                stage=failed_stage,
                outcome=Outcome.FAILURE,
                tenant_id=event.tenant_id,
                trace_id=event.trace_id,
                bronze_id=ctx.bronze_id,
                mapping_version_id=ctx.mapping_version_id,
                duration_ms=ctx.lap(),
                failure_code=failure_code,
                failure_message=str(exc)[:2000],
                # The class name is always preserved (the no-information-loss rule
                # behind the INFRA_FAILURE fallback; harmless for mapped codes).
                event_data={"exception_class": type(exc).__name__},
            )
            if not _quarantinable(exc, failure_code, ctx):
                raise
            try:
                await self.quarantine.hold_chunk_failure(
                    event,
                    ctx,
                    stage=failed_stage,
                    failure_code=failure_code,
                    message=str(exc)[:2000],
                    exception_class=type(exc).__name__,
                )
            except Exception as hold_exc:
                # Fail-loud quarantine posture: the held thing did NOT land, so the
                # message must stay live. Log the hold failure with context, then
                # re-raise the ORIGINAL failure (hold failure as explicit cause) so
                # the subscriber nacks exactly as before 11a (the Pub/Sub
                # dead-letter policy backstops the loop).
                _log.bind(
                    stage="quarantine", tenant_id=str(event.tenant_id), trace_id=str(event.trace_id)
                ).error(
                    "quarantine hold failed for %s; falling back to nack (never ack-and-lose)",
                    failure_code,
                    exc_info=True,
                )
                raise exc from hold_exc
            await self._emit_quarantined(
                event,
                ctx,
                failed_stage=failed_stage,
                failure_code=failure_code,
                table="quarantined_chunks",
                held=1,
                message=str(exc)[:2000],
            )
            return ConsumeOutcome(disposition="quarantined")

    async def _process(self, event: IngressReadyEvent, ctx: _FlowContext) -> ConsumeOutcome:
        log = _log.bind(stage="pipeline", tenant_id=str(event.tenant_id), trace_id=str(event.trace_id))
        tenant_id, trace_id = event.tenant_id, event.trace_id

        # 0. Redelivery detection (Slice 30b): best-effort audit readback so a
        #    redelivered chunk's intake is legible as RETRIED rather than an
        #    indistinguishable duplicate SUCCESS row.
        seen_before = await self._seen_before(tenant_id, trace_id)

        # 1. Fetch (intake + bronze + GCS; the chunk arrives tokenized, D24).
        #    ctx learns the bronze identity MID-stage via note_bronze, so a
        #    download/parse failure still classifies as post-fetch (11a guard).
        fetched = await self._staged(
            Stage.RECEIVED,
            fetch_chunk(
                self.engine,
                self.storage,
                event,
                bronze_bucket=self.bronze_bucket,
                on_bronze=ctx.note_bronze,
            ),
        )
        ctx.row_count = fetched.frame.height
        await self.audit.emit(
            stage=Stage.RECEIVED,
            outcome=Outcome.RETRIED if seen_before else Outcome.SUCCESS,
            tenant_id=tenant_id,
            trace_id=trace_id,
            bronze_id=fetched.bronze.bronze_id,
            row_count=fetched.frame.height,
            duration_ms=ctx.lap(),
            event_data={"columns": len(fetched.frame.columns), "dis_channel": fetched.bronze.dis_channel},
        )

        # 2. Mapping load (per-lookup side-input, D6) + routing.
        loaded = await self._staged(Stage.MAPPING_LOOKED_UP, load_active_mapping(self.engine, event))
        ctx.mapping_version_id = loaded.mapping_version_id
        await self.audit.emit(
            stage=Stage.MAPPING_LOOKED_UP,
            outcome=Outcome.SUCCESS,
            tenant_id=tenant_id,
            trace_id=trace_id,
            bronze_id=fetched.bronze.bronze_id,
            mapping_version_id=loaded.mapping_version_id,
            duration_ms=ctx.lap(),
            event_data={
                "target_model": loaded.target_model.__name__,
                "source_id": event.source_id,
                # Slice 8a (D71): the template the lookup keyed on, additive.
                "template_id": str(event.template_id),
            },
        )

        # 3. Sale + catalogue paths: the store row's enrichment facts (a data read,
        #    not identity validation — D39's FK is the enforcement; no Identity
        #    Service, D28). One widened read (D95): tax_treatment feeds the event-path
        #    injection (unchanged) AND the current-position enrichment (D98); currency
        #    feeds the current-position enrichment (D95).
        store_facts: StoreFacts | None = None
        if loaded.target_model in (StoreSkuSaleEvent, StoreSkuCurrentPosition):
            store_facts = await self._staged(Stage.MAPPING_LOOKED_UP, read_store_facts(self.engine, event))
        tax_treatment = store_facts.tax_treatment if store_facts is not None else None

        # 4. Pre-mapping (source-shape) gate — D13: semantic validation lives here.
        pre = run_pre_validation(loaded, fetched.frame, tenant_id=str(tenant_id), trace_id=str(trace_id))
        if not pre.passed:
            pre_failures: list[GateFailure] = [
                (f.column, f.row_index, f.check, f.reason) for f in pre.failures
            ]
            await self._emit_gate_failure(
                Stage.PRE_MAPPING_VALIDATED, event, fetched, loaded, ctx, failures=pre_failures
            )
            return await self._quarantine_gate_failure(
                Stage.PRE_MAPPING_VALIDATED,
                event,
                ctx,
                failures=pre_failures,
                fallback="failed_pre_validation",
            )
        await self.audit.emit(
            stage=Stage.PRE_MAPPING_VALIDATED,
            outcome=Outcome.SUCCESS,
            tenant_id=tenant_id,
            trace_id=trace_id,
            bronze_id=fetched.bronze.bronze_id,
            mapping_version_id=loaded.mapping_version_id,
            row_count=fetched.frame.height,
            duration_ms=ctx.lap(),
        )

        # 5. The four sub-stages (pure engine). ANY failed cell fails the chunk —
        #    the minimal disposition has no per-row split (Slice 11).
        result = apply_loaded_mapping(loaded, fetched.frame, tenant_id=str(tenant_id), trace_id=str(trace_id))
        if result.failures:
            map_failures: list[GateFailure] = [
                (f.column, f.row_index, f"{f.stage}:{f.op}", f.reason) for f in result.failures
            ]
            await self._emit_gate_failure(
                Stage.MAPPING_EXECUTED, event, fetched, loaded, ctx, failures=map_failures
            )
            return await self._quarantine_gate_failure(
                Stage.MAPPING_EXECUTED,
                event,
                ctx,
                failures=map_failures,
                fallback="failed_mapping",
            )
        await self.audit.emit(
            stage=Stage.MAPPING_EXECUTED,
            outcome=Outcome.SUCCESS,
            tenant_id=tenant_id,
            trace_id=trace_id,
            bronze_id=fetched.bronze.bronze_id,
            mapping_version_id=loaded.mapping_version_id,
            row_count=fetched.frame.height,
            rows_succeeded=result.contribution.height,
            rows_failed=0,
            duration_ms=ctx.lap(),
        )

        # 5b. Enrichment (D94): the lib's value wins over the mapping for its
        #     registered fields, applied BEFORE post-validation so enriched values
        #     pass the canonical-shape gate and count toward completeness. Gated to
        #     the current-position target — the event path is untouched (D98).
        if loaded.target_model is StoreSkuCurrentPosition:
            assert store_facts is not None  # read above for this target (FK-guaranteed)
            result = apply_loaded_enrichment(
                {"currency": store_facts.currency, "tax_treatment": store_facts.tax_treatment},
                result,
                tenant_id=str(tenant_id),
                trace_id=str(trace_id),
            )

        # 6. Post-mapping (canonical-shape) gate + the drift guard (ERRORS, never skips).
        post = run_post_validation(
            loaded, result.contribution, tenant_id=str(tenant_id), trace_id=str(trace_id)
        )
        if not post.passed:
            post_failures: list[GateFailure] = [
                (f.column, f.row_index, f.check, f.reason) for f in post.failures
            ]
            await self._emit_gate_failure(
                Stage.POST_MAPPING_VALIDATED, event, fetched, loaded, ctx, failures=post_failures
            )
            return await self._quarantine_gate_failure(
                Stage.POST_MAPPING_VALIDATED,
                event,
                ctx,
                failures=post_failures,
                fallback="failed_post_validation",
            )
        await self.audit.emit(
            stage=Stage.POST_MAPPING_VALIDATED,
            outcome=Outcome.SUCCESS,
            tenant_id=tenant_id,
            trace_id=trace_id,
            bronze_id=fetched.bronze.bronze_id,
            mapping_version_id=loaded.mapping_version_id,
            row_count=result.contribution.height,
            duration_ms=ctx.lap(),
        )

        # 7. mapping_version_id stamp (D22) + the write. Two SIBLING paths (Slice
        #    14d): the catalogue (snapshot) path writes the hot table directly via
        #    the reused complete-path upsert (no event table, no dedup); the event
        #    path is the UNCHANGED atomic dual-write (D30).
        if loaded.target_model is StoreSkuCurrentPosition:
            report = await self._staged(
                Stage.CANONICAL_WRITTEN,
                write_catalogue_chunk(
                    self.engine,
                    event,
                    loaded,
                    result,
                    dis_channel=fetched.bronze.dis_channel,
                    batch_size=self.batch_size,
                ),
            )
        else:
            rows = build_event_rows(event, fetched.bronze, loaded, result, tax_treatment=tax_treatment)
            report = await self._staged(
                Stage.CANONICAL_WRITTEN,
                write_chunk(
                    self.engine,
                    event,
                    loaded,
                    rows,
                    dis_channel=fetched.bronze.dis_channel,
                    tax_treatment=tax_treatment,
                    batch_size=self.batch_size,
                ),
            )
        await self.audit.emit(
            stage=Stage.CANONICAL_WRITTEN,
            outcome=Outcome.SUCCESS,
            tenant_id=tenant_id,
            trace_id=trace_id,
            bronze_id=fetched.bronze.bronze_id,
            mapping_version_id=loaded.mapping_version_id,
            row_count=report.event_rows_written,
            rows_succeeded=report.event_rows_written,
            duration_ms=ctx.lap(),
            event_data={
                "written_to_table": report.written_to_table,
                "hot_rows_upserted": report.hot_rows_upserted,
                "hot_noops": report.hot_noops,
                "batches": report.batches,
                "duplicates": len(report.duplicates),
            },
        )

        # 8. D42 duplicate detail: one ROW-scoped event per dedup-key hit.
        for hit in report.duplicates:
            await self.audit.emit_duplicate(
                hit,
                tenant_id=tenant_id,
                store_id=event.store_id,
                source_id=event.source_id,
                trace_id=trace_id,
                bronze_id=fetched.bronze.bronze_id,
                mapping_version_id=loaded.mapping_version_id,
            )

        log.info(
            "chunk written: %s event rows, %s hot upserts, %s duplicates",
            report.event_rows_written,
            report.hot_rows_upserted,
            len(report.duplicates),
        )
        return ConsumeOutcome(disposition="written", report=report)

    async def _emit_gate_failure(
        self,
        stage: Stage,
        event: IngressReadyEvent,
        fetched: FetchedChunk,
        loaded: LoadedMapping,
        ctx: _FlowContext,
        *,
        failures: list[tuple[str | None, int | None, str, str]],
    ) -> None:
        """One INGRESS_EVENT FAILURE summary + ROW-scoped events (Option B).

        Slice 30b stable vocabulary: the summary carries the per-gate
        ``FailureCode``; ROW events carry ``VALIDATION_ROW_FAILED`` with the
        pandera check name in ``event_data["check"]`` (an unbounded vocabulary
        cannot be enum members; column/reason stay in ``failure_message``).
        """
        await self.audit.emit(
            stage=stage,
            outcome=Outcome.FAILURE,
            tenant_id=event.tenant_id,
            trace_id=event.trace_id,
            bronze_id=fetched.bronze.bronze_id,
            mapping_version_id=loaded.mapping_version_id,
            row_count=fetched.frame.height,
            rows_failed=len(failures),
            duration_ms=ctx.lap(),
            failure_code=_GATE_FAILURE_CODES[stage],
            failure_message=f"{len(failures)} failure(s); chunk nacked (Slice 10 minimal disposition)",
        )
        for column, row_index, check, reason in failures:
            await self.audit.emit(
                stage=stage,
                outcome=Outcome.FAILURE,
                scope=EventScope.ROW,
                tenant_id=event.tenant_id,
                trace_id=event.trace_id,
                bronze_id=fetched.bronze.bronze_id,
                mapping_version_id=loaded.mapping_version_id,
                row_offset=row_index,
                failure_code=FailureCode.VALIDATION_ROW_FAILED,
                event_data={"check": check[:256]},
                # Reasons are column/check-grained and never carry cell values
                # (dis-validation/dis-mapping contract: values are quarantine
                # payload, never logged or audited here).
                failure_message=f"column={column}: {reason}"[:2000],
            )

    async def _quarantine_gate_failure(
        self,
        stage: Stage,
        event: IngressReadyEvent,
        ctx: _FlowContext,
        *,
        failures: list[GateFailure],
        fallback: Disposition,
    ) -> ConsumeOutcome:
        """Hold a gate's failures and ACK, or fall back to today's nack disposition.

        Gate failures are data-deterministic (the same chunk re-validates
        identically), so they are storm-class too. Routing (Slice 11a):

        - every failure carries a ``row_index`` → one ``quarantined_rows`` record
          per distinct failing row (``VALIDATION_ROW_FAILED``). The chunk's GOOD
          rows are NOT written — the whole-chunk processing model is unchanged
          (no partial success exists; bronze remains the recoverable source for a
          future replay).
        - ANY row-less failure (missing column / dtype / frame-grain — the shape
          that cannot satisfy ``quarantined_rows.row_offset NOT NULL``) → the
          chunk is held whole in ``quarantined_chunks`` under the gate-summary
          code.

        A failed HOLD logs and returns ``fallback`` (the pre-11a ``failed_*``
        disposition → nack): never ack-and-lose. The QUARANTINED audit emit
        happens only after the hold succeeded, and is fire-and-forget.
        """
        gate_code = _GATE_FAILURE_CODES[stage]
        try:
            if all(row_index is not None for _, row_index, _, _ in failures):
                held = await self.quarantine.hold_row_failures(event, ctx, stage=stage, failures=failures)
                table = "quarantined_rows"
                code: FailureCode = FailureCode.VALIDATION_ROW_FAILED
            else:
                await self.quarantine.hold_chunk_failure(
                    event,
                    ctx,
                    stage=stage,
                    failure_code=gate_code,
                    message=f"{len(failures)} gate failure(s); row-less shape held at chunk grain",
                    failures=failures,
                )
                held, table, code = 1, "quarantined_chunks", gate_code
        except Exception:
            _log.bind(stage="quarantine", tenant_id=str(event.tenant_id), trace_id=str(event.trace_id)).error(
                "quarantine hold failed for %s gate; falling back to nack (never ack-and-lose)",
                gate_code,
                exc_info=True,
            )
            return ConsumeOutcome(disposition=fallback)
        await self._emit_quarantined(
            event,
            ctx,
            failed_stage=stage,
            failure_code=code,
            table=table,
            held=held,
            message=f"{len(failures)} gate failure(s) held; chunk acked (Slice 11a)",
        )
        return ConsumeOutcome(disposition="quarantined")

    async def _emit_quarantined(
        self,
        event: IngressReadyEvent,
        ctx: _FlowContext,
        *,
        failed_stage: Stage,
        failure_code: FailureCode,
        table: str,
        held: int,
        message: str | None = None,
    ) -> None:
        """The QUARANTINED disposition record (the Slice 11a emitter for the
        reserved stage), carrying the D78 failure-audit shape.

        ``outcome=SUCCESS``: the FAILURE row already recorded the failure at the
        stage it died in; this row records that the quarantine ACTION succeeded —
        the trail reads "failed at stage X → QUARANTINED". Fire-and-forget like
        every audit emit (hard rule 11): a failed emit never blocks the ack of a
        successfully-held chunk.
        """
        await self.audit.emit(
            stage=Stage.QUARANTINED,
            outcome=Outcome.SUCCESS,
            tenant_id=event.tenant_id,
            trace_id=event.trace_id,
            bronze_id=ctx.bronze_id,
            mapping_version_id=ctx.mapping_version_id,
            row_count=ctx.row_count,
            rows_failed=held if table == "quarantined_rows" else None,
            duration_ms=ctx.lap(),
            failure_code=failure_code,
            failure_message=message,
            event_data={
                "quarantine_table": table,
                "held": held,
                "failed_stage": failed_stage.value,
            },
        )

    async def _seen_before(self, tenant_id: UUID, trace_id: UUID) -> bool:
        """Best-effort redelivery detection for the RETRIED outcome (Slice 30b).

        One indexed read (``ix_audit_events_trace_id``) for a prior RECEIVED row
        emitted by THIS service for the same trace. Fire-and-forget like every
        audit concern: a read failure degrades to ``False`` (the intake emits
        SUCCESS) — it never wedges or fails the data path. Best-effort by design:
        audit is itself fire-and-forget, so a missing prior row simply re-reads
        as a first delivery (D44 tolerates the duplicate-shaped result).

        Upgrade path (the quarantine work): once a dead-letter policy exists on
        the subscription, Pub/Sub populates ``delivery_attempt`` on the pull
        response — a transport-level signal that replaces this readback.
        """
        try:
            async with rls_session(self.engine, tenant_id) as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM audit.events "
                            "WHERE trace_id = :trace_id AND service_name = :service "
                            "AND stage = 'RECEIVED' LIMIT 1"
                        ),
                        {"trace_id": trace_id, "service": SERVICE_NAME},
                    )
                ).first()
            return row is not None
        except Exception:  # noqa: BLE001 — audit-side read; never blocks the data path
            _log.bind(stage="redelivery_check", tenant_id=str(tenant_id), trace_id=str(trace_id)).warning(
                "redelivery readback failed; assuming first delivery (best-effort)", exc_info=True
            )
            return False

    @staticmethod
    async def _staged[T](stage: Stage, awaitable: Awaitable[T]) -> T:
        """Tag in-flight exceptions with the stage they died in (for the FAILURE audit)."""
        try:
            return await awaitable
        except Exception as exc:
            exc._dis_stage = stage  # type: ignore[attr-defined]  # noqa: SLF001
            raise
