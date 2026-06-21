"""dis-audit — audit-event model, Cloud SQL writer, and the owned vocabulary.

Four responsibilities (build-guide Slice 6):

- :class:`AuditEvent` — one ``audit.events`` row, hand-aligned to the live schema.
- :class:`PostgresAuditWriter` — the Phase-1 Cloud SQL writer, fire-and-forget (hard
  rule 11): failures are logged with context and never raised to or block the caller.
- The backend-selecting :class:`AuditWriter` interface + :func:`select_writer`, with the
  inert Phase-3 :class:`BigQueryAuditWriter` seam behind ``BqClient`` (``decisions.md`` D34).
- The :class:`Stage` / :class:`EventScope` / :class:`Outcome` vocabulary consumers import,
  plus the :class:`FailureCode` stable failure vocabulary and its
  :func:`failure_code_for` exception registry (Slice 30b).

No service emits audit events here; emission is service-layer (Slice 7 onward). Every DIS
audit event carries a known ``tenant_id`` (``decisions.md`` D43).
"""

from __future__ import annotations

from dis_audit.bigquery_writer import BigQueryAuditWriter
from dis_audit.event import AuditEvent
from dis_audit.failure_codes import FailureCode, failure_code_for
from dis_audit.postgres_writer import PostgresAuditWriter
from dis_audit.schema_contract import EXPECTED_COLUMNS, ColumnSpec, diff_schema
from dis_audit.stages import EventScope, Outcome, Stage
from dis_audit.writer import AuditBackend, AuditWriter, select_writer

__all__ = [
    "EXPECTED_COLUMNS",
    "AuditBackend",
    "AuditEvent",
    "AuditWriter",
    "BigQueryAuditWriter",
    "ColumnSpec",
    "EventScope",
    "FailureCode",
    "Outcome",
    "PostgresAuditWriter",
    "Stage",
    "diff_schema",
    "failure_code_for",
    "select_writer",
]
