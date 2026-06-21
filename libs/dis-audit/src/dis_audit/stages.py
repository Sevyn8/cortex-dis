"""The audit vocabulary ``dis-audit`` owns and its service consumers import.

Three closed string enums. Slices 8-18 import these rather than re-declaring stage /
scope / outcome strings, so the vocabulary stays consistent across services (Slice 5
and Slice 10 import :class:`Stage` / :class:`Outcome` from here).

Why closed enums and not free strings: ``audit.events`` constrains ``event_scope`` and
``outcome`` with CHECK constraints, but ``stage`` has **no** CHECK (it is a free
``varchar(64)`` in the live schema). Closure of :class:`Stage` is therefore a
``dis-audit`` type-level guarantee — the lib is the vocabulary's owner — not a DB
constraint. :class:`EventScope` and :class:`Outcome` mirror the live CHECK vocab
exactly (introspected from ``ck_audit_events_event_scope_vocab`` /
``ck_audit_events_outcome_vocab``); the integration drift guard asserts that match.

Note on duplicate outcomes (the D42 REVISION, Slice 30c): Slice 10 deliberately
resolved D42 by keeping the duplicate detail (``DUPLICATE_*``, ``prior_trace_id``,
``row_hash``, ``dedup_key``) in ``event_data`` JSONB within the then-4-value CHECK.
Slice 30c supersedes that resolution — the audit/quarantine consoles query by the
duplicate distinction — so ``DUPLICATE_NOOP`` / ``DUPLICATE_OVERWRITTEN`` are now
first-class :class:`Outcome` members mirroring the live 6-value CHECK, and
``prior_trace_id`` is a live column. The pair REFINES SUCCESS (the append-only
insert genuinely landed, D33); ``row_hash``/``dedup_key`` stay in ``event_data``.
"""

from __future__ import annotations

from enum import StrEnum


class EventScope(StrEnum):
    """Audit-event scope distinguisher. Mirrors ``ck_audit_events_event_scope_vocab``.

    INGRESS_EVENT — per-stage summary for one chunk. ROW — a per-row record (typically
    a failure). Volume scales with failure rate, not row count (architecture glossary).
    """

    INGRESS_EVENT = "INGRESS_EVENT"
    ROW = "ROW"


class Outcome(StrEnum):
    """A stage's result for one scope. Mirrors ``ck_audit_events_outcome_vocab`` exactly.

    The DUPLICATE_* pair (Slice 30c, the D42 revision) refines SUCCESS: a dedup-key
    hit's append-only insert genuinely landed (D33); the kind is queryable as the
    outcome instead of an ``event_data`` key.
    """

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    SKIPPED = "SKIPPED"
    RETRIED = "RETRIED"
    DUPLICATE_NOOP = "DUPLICATE_NOOP"
    DUPLICATE_OVERWRITTEN = "DUPLICATE_OVERWRITTEN"


class Stage(StrEnum):
    """The pipeline stage an audit event records. Closed, owned vocabulary.

    Membership is the full **Phase-1** pipeline stage set, so importers across slices
    bind to a stable enum instead of extending it per slice. The cutoff is the phase
    boundary, not the slice boundary: Phase-3-only stages (``BQ_EXPORTED``,
    ``PARTITION_DROPPED`` — nightly-batch, Slice 21) are deliberately excluded as dead
    Phase-3 surface, mirroring the seam discipline.

    Sources: ``schemas/postgres/audit/events.sql`` header and the BigQuery
    ``audit_events`` ``stage`` description.
    """

    # Receiver / ingress stages (csv-ingest-worker; deferred receiver services).
    RECEIVED = "RECEIVED"
    PII_TOKENIZED = "PII_TOKENIZED"
    BRONZE_WRITTEN = "BRONZE_WRITTEN"
    INGRESS_PUBLISHED = "INGRESS_PUBLISHED"
    # Streaming-consumer stages.
    MAPPING_LOOKED_UP = "MAPPING_LOOKED_UP"
    IDENTITY_VALIDATED = "IDENTITY_VALIDATED"
    PRE_MAPPING_VALIDATED = "PRE_MAPPING_VALIDATED"
    MAPPING_EXECUTED = "MAPPING_EXECUTED"
    POST_MAPPING_VALIDATED = "POST_MAPPING_VALIDATED"
    CANONICAL_WRITTEN = "CANONICAL_WRITTEN"
    QUARANTINED = "QUARANTINED"
    # Daily-compute (Slice 18).
    SIGNAL_COMPUTED = "SIGNAL_COMPUTED"
