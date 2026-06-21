"""dis-quarantine — quarantine record models, the fail-loud Cloud SQL writer, and
the ``failure_stage`` vocabulary (Slice 11a).

Three responsibilities:

- :class:`QuarantinedChunk` / :class:`QuarantinedRow` — the write shape of the two
  live ``quarantine.*`` tables (lifecycle columns deliberately absent: this slice
  writes ``status=NEW`` only, stamped by the DB default).
- :class:`PostgresQuarantineWriter` — ``hold_chunk`` / ``hold_rows``, **fail-loud**
  (the deliberate asymmetry with dis-audit: audit = the record = fire-and-forget;
  quarantine = the held thing = raise, so the caller nacks and never acks-and-loses).
- :class:`QuarantineFailureStage` + :func:`failure_stage_for` — the live CHECK
  vocabulary and the total mapping from the audit :class:`~dis_audit.Stage`.

No service decides WHAT to quarantine here; the allowlist is the caller's
(streaming-consumer in 11a; the worker adopts this lib later). Every record
carries a known ``tenant_id`` and is written under ``rls_session`` (hard rules
1/12); the FORCE-RLS ``tenant_isolation`` policies scope reads and writes.
"""

from __future__ import annotations

from dis_quarantine.failure_stages import (
    ROW_FAILURE_STAGES,
    QuarantineFailureStage,
    failure_stage_for,
)
from dis_quarantine.postgres_writer import PostgresQuarantineWriter
from dis_quarantine.records import QuarantinedChunk, QuarantinedRow

__all__ = [
    "ROW_FAILURE_STAGES",
    "PostgresQuarantineWriter",
    "QuarantineFailureStage",
    "QuarantinedChunk",
    "QuarantinedRow",
    "failure_stage_for",
]
