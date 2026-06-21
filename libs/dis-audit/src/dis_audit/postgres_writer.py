"""The Phase-1 Cloud SQL audit writer — fire-and-forget.

Lands one ``audit.events`` row through the RLS-aware session (``dis-rls``), so the
target-safety guard (``current_database()`` must be ``ithina_dis_db``; the role must not
bypass RLS) is inherited, not reinvented, and the tenant scope required by the live
``rls_audit_events_tenant`` policy is set.

Fire-and-forget (hard rule 11, the one sanctioned exception to code-quality rule 6): a
write failure is logged with ``tenant_id`` / ``trace_id`` / ``stage`` and reported as
``False`` — never raised to the caller, never blocking the data path. The swallow is
explicit and scoped to this write path only. A missing partition, a missing grant, or a
schema mismatch is logged as an **error worth alerting** (``decisions.md`` D45), not
absorbed as routine.

Product rule (``decisions.md`` D43): every DIS audit event carries a known ``tenant_id``;
there is no tenant-less audit path. A ``None`` tenant is refused loudly (logged
``AuditWriteError``), never silently dropped.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_audit.event import AuditEvent
from dis_core.logging import get_logger
from dis_rls import rls_session

_SERVICE = "dis-audit"
_log = get_logger(_SERVICE)

# Static INSERT over the 22 caller-supplied columns; id and _loaded_at are server-defaulted
# (uuidv7() / now()) and deliberately omitted so the DB stamps them. event_data is cast to
# JSONB from a JSON string and client_ip to INET, matching the seeder's CAST precedent.
_INSERT = text(
    """
    INSERT INTO audit.events (
        event_timestamp, event_date, trace_id, prior_trace_id, tenant_id,
        data_ingress_event_id,
        service_name, service_version, stage, event_scope, outcome,
        row_count, rows_succeeded, rows_failed, duration_ms, row_offset,
        mapping_version_id, failure_code, failure_message, event_data,
        auth_principal, client_ip
    ) VALUES (
        :event_timestamp, :event_date, :trace_id, :prior_trace_id, :tenant_id,
        :data_ingress_event_id,
        :service_name, :service_version, :stage, :event_scope, :outcome,
        :row_count, :rows_succeeded, :rows_failed, :duration_ms, :row_offset,
        :mapping_version_id, :failure_code, :failure_message, CAST(:event_data AS JSONB),
        :auth_principal, CAST(:client_ip AS INET)
    )
    """
)


class PostgresAuditWriter:
    """Writes audit events to Cloud SQL ``audit.events``. Fire-and-forget.

    The caller owns the engine (``dis-rls`` discipline: no hidden process-wide engine);
    create it with ``dis_rls.create_rls_engine`` in app lifespan / a loop-scoped fixture.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def write(self, event: AuditEvent) -> bool:
        """Land ``event``. Returns ``True`` on a confirmed write, ``False`` on a logged failure."""
        log = _log.bind(
            stage=str(event.stage),
            tenant_id=None if event.tenant_id is None else str(event.tenant_id),
            trace_id=str(event.trace_id),
        )
        # Product rule D43 — a CALLER CONTRACT violation, not an infrastructure failure: every
        # DIS audit event carries a known tenant_id. Logged distinctly (so it is not buried under
        # the infra-failure message below) and dropped — but NOT raised, because audit emission
        # must never block the data path even on a caller bug (hard rule 11). The schema's
        # nullable column is intentional headroom, not a supported path.
        if event.tenant_id is None:
            log.error(
                "audit event refused: no tenant_id. DIS has no tenant-less audit path "
                "(decisions.md D43) — caller contract violation, not an infrastructure failure. "
                "Event dropped; fix the caller.",
                extra={"emitting_service": event.service_name, "contract_violation": "D43"},
            )
            return False
        try:
            async with rls_session(self._engine, event.tenant_id) as conn:
                await conn.execute(_INSERT, event.to_insert_params())
            return True
        except Exception:  # noqa: BLE001 — sanctioned fire-and-forget swallow (hard rule 11)
            log.error(
                "audit write failed; event dropped (fire-and-forget). A missing partition, "
                "missing grant, or schema mismatch here is worth alerting (decisions.md D45)",
                extra={"emitting_service": event.service_name, "failure_code": event.failure_code},
                exc_info=True,
            )
            return False
