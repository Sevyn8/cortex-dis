"""The Cloud SQL quarantine writer — fail-loud, the OPPOSITE of dis-audit's posture.

Lands ``quarantine.quarantined_chunks`` / ``quarantined_rows`` rows through the
RLS-aware session (``dis-rls``), inheriting the target-safety guard
(``current_database()`` must be ``ithina_dis_db``; the role must not bypass RLS)
and the tenant scope the live FORCE-RLS ``tenant_isolation`` policies require.

**Fail-loud (Slice 11a posture, the deliberate asymmetry with dis-audit):** audit
is the RECORD of what happened (fire-and-forget, hard rule 11); quarantine is the
HELD THING itself — the data path. A failed quarantine write therefore RAISES
:class:`~dis_core.errors.QuarantineWriteError` so the caller keeps the message
live (nack; the Pub/Sub dead-letter policy backstops). Swallowing here would be
ack-and-lose.

This slice writes ``status=NEW`` only — the DB default stamps it; the record
models cannot express a lifecycle transition at all.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_core.errors import QuarantineWriteError
from dis_core.logging import get_logger
from dis_quarantine.records import QuarantinedChunk, QuarantinedRow
from dis_rls import rls_session

_SERVICE = "dis-quarantine"
_log = get_logger(_SERVICE)

# Static INSERTs over the caller-supplied columns; id / status / last_updated_at are
# server-defaulted (uuidv7() / 'NEW' / now()) and deliberately omitted so the DB
# stamps them. failure_context is cast to JSONB from a JSON string (the dis-audit
# event_data precedent).
_INSERT_CHUNK = text(
    """
    INSERT INTO quarantine.quarantined_chunks (
        tenant_id, store_id, data_ingress_event_id, trace_id,
        source_id, dis_channel, gcs_uri,
        failure_stage, failure_reason, failure_context,
        mapping_version_id, row_count_in_chunk, quarantined_at
    ) VALUES (
        :tenant_id, :store_id, :data_ingress_event_id, :trace_id,
        :source_id, :dis_channel, :gcs_uri,
        :failure_stage, :failure_reason, CAST(:failure_context AS JSONB),
        :mapping_version_id, :row_count_in_chunk, :quarantined_at
    )
    """
)

_INSERT_ROW = text(
    """
    INSERT INTO quarantine.quarantined_rows (
        tenant_id, store_id, data_ingress_event_id, trace_id,
        source_id, dis_channel, gcs_uri, row_offset, row_sha256,
        failure_stage, failure_reason, failure_context,
        mapping_version_id, quarantined_at
    ) VALUES (
        :tenant_id, :store_id, :data_ingress_event_id, :trace_id,
        :source_id, :dis_channel, :gcs_uri, :row_offset, :row_sha256,
        :failure_stage, :failure_reason, CAST(:failure_context AS JSONB),
        :mapping_version_id, :quarantined_at
    )
    """
)


class PostgresQuarantineWriter:
    """Writes held failures to the ``quarantine.*`` tables. Fail-loud.

    The caller owns the engine (``dis-rls`` discipline: no hidden process-wide
    engine); create it with ``dis_rls.create_rls_engine`` in app lifespan / a
    loop-scoped fixture.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def hold_chunk(self, record: QuarantinedChunk) -> None:
        """Land one held chunk, or raise :class:`QuarantineWriteError` loudly."""
        log = _log.bind(
            stage=str(record.failure_stage),
            tenant_id=str(record.tenant_id),
            trace_id=str(record.trace_id),
        )
        try:
            async with rls_session(self._engine, record.tenant_id) as conn:
                await conn.execute(_INSERT_CHUNK, record.to_insert_params())
        except Exception as exc:
            log.error(
                "quarantined_chunks write failed; raising so the message is NACKED, "
                "never acked-and-lost (Slice 11a fail-loud posture)",
                extra={"failure_reason": record.failure_reason},
                exc_info=True,
            )
            raise QuarantineWriteError(
                f"quarantine.quarantined_chunks insert failed: {exc}",
                tenant_id=str(record.tenant_id),
                trace_id=str(record.trace_id),
                failure_code=record.failure_reason,
            ) from exc
        log.info("chunk held in quarantine (status=NEW): %s", record.failure_reason)

    async def hold_rows(self, records: Sequence[QuarantinedRow]) -> None:
        """Land held rows in ONE tenant-scoped transaction, or raise loudly.

        All records must share one ``tenant_id`` (one chunk's rows — one RLS
        scope); mixed tenants are a caller bug and raise before any write.
        An empty sequence is likewise a caller bug (the call site routes only
        when failures exist), never a silent no-op.
        """
        if not records:
            raise QuarantineWriteError("hold_rows called with no records (caller bug)")
        tenants = {record.tenant_id for record in records}
        if len(tenants) > 1:
            raise QuarantineWriteError(
                f"hold_rows records span {len(tenants)} tenants; one chunk's rows share one tenant scope",
                trace_id=str(records[0].trace_id),
            )
        first = records[0]
        log = _log.bind(
            stage=str(first.failure_stage),
            tenant_id=str(first.tenant_id),
            trace_id=str(first.trace_id),
        )
        try:
            async with rls_session(self._engine, first.tenant_id) as conn:
                await conn.execute(_INSERT_ROW, [record.to_insert_params() for record in records])
        except QuarantineWriteError:
            raise
        except Exception as exc:
            log.error(
                "quarantined_rows write failed (%d records); raising so the message is "
                "NACKED, never acked-and-lost (Slice 11a fail-loud posture)",
                len(records),
                extra={"failure_reason": first.failure_reason},
                exc_info=True,
            )
            raise QuarantineWriteError(
                f"quarantine.quarantined_rows insert failed ({len(records)} records): {exc}",
                tenant_id=str(first.tenant_id),
                trace_id=str(first.trace_id),
                failure_code=first.failure_reason,
            ) from exc
        log.info("%d row(s) held in quarantine (status=NEW): %s", len(records), first.failure_reason)
