"""audit.events: DUPLICATE_* outcomes + prior_trace_id — the D42 REVISION (Slice 30c)

D42 was RESOLVED by Slice 10 via a DELIBERATE choice: the duplicate detail
(DUPLICATE_NOOP/DUPLICATE_OVERWRITTEN, prior_trace_id, row_hash, dedup_key)
lives in event_data JSONB, honouring the then-4-value outcome CHECK — promotion
was "rejected as over-engineering for v1.0" (decisions.md, the Slice-10 register
note). This migration consciously SUPERSEDES that resolution — D42 is revised,
not broken — because the audit and quarantine consoles need to QUERY by the
duplicate distinction and by "what redelivered from what":

- ``ck_audit_events_outcome_vocab`` extends from 4 to 6 values, adding
  DUPLICATE_NOOP and DUPLICATE_OVERWRITTEN. The pair REFINES SUCCESS (the
  append-only insert genuinely landed, D33); they are not failures.
- ``prior_trace_id`` (uuid, NULL) is added — the PRIOR delivery's trace on a
  duplicate row, previously an event_data key.

Only the queried-by fields are promoted: ``row_hash`` and ``dedup_key`` STAY in
event_data.

ADDITIVE on the plain audit.events (Slice 30a, D77) — the table now accrues
real rows, so this is ADD COLUMN + a CHECK swap, never a drop-recreate. Both
steps are gated (column existence / current constraint definition), so the
migration is a TRUE NO-OP on a manifest-fresh database where 0001 already
applied the updated schemas/postgres/audit/events.sql — fresh == migrated.

downgrade() REFUSES LOUDLY (the 0005 precedent) when rows with DUPLICATE_*
outcomes exist: restoring the 4-value CHECK over violating rows would either
fail at ADD CONSTRAINT or silently corrupt the vocabulary contract; the
operator must disposition those rows first. With none present it restores the
4-value CHECK and drops the column.

See: docs/slices/slice-30c-audit-tier2.md, decisions.md D42 (revised here),
D33/D34/D44, D77/D78/D79, hard rule 11 (writer posture unchanged).

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-06

"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# Target-safety guard (the 0007 pattern). Expected DIS database name, with the
# Customer Master database hard-blocked regardless.
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"

_CHECK_NAME = "ck_audit_events_outcome_vocab"
_OLD_VOCAB = ("SUCCESS", "FAILURE", "SKIPPED", "RETRIED")
_NEW_VOCAB = (*_OLD_VOCAB, "DUPLICATE_NOOP", "DUPLICATE_OVERWRITTEN")


def _exec(sql: str) -> None:
    """Execute raw SQL via the DBAPI, bypassing SQLAlchemy text() bind parsing
    (the DDL contains '::' casts psycopg should see verbatim)."""
    op.get_bind().exec_driver_sql(sql)


def _scalar(sql: str) -> object:
    return op.get_bind().exec_driver_sql(sql).scalar()


def check_migration_target(current: str, *, expected_db: str = _EXPECTED_DB, cm_db: str = _CM_DB) -> None:
    """Pure target check: refuse Customer Master outright, require the DIS database."""
    if current == cm_db:
        raise RuntimeError(
            f"Refusing to run DIS migration: connected to the Customer Master "
            f"database '{current}'. Point POSTGRES_ADMIN_URL at the DIS database."
        )
    if current != expected_db:
        raise RuntimeError(
            f"Refusing to run DIS migration: connected to '{current}' but expected "
            f"DIS database '{expected_db}' (POSTGRES_DB). Check POSTGRES_ADMIN_URL."
        )


def _guard_target() -> None:
    current = _scalar("SELECT current_database()")
    check_migration_target(str(current))


def _column_exists() -> bool:
    return bool(
        _scalar(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = 'audit' AND table_name = 'events' "
            "AND column_name = 'prior_trace_id'"
        )
    )


def _outcome_check_def() -> str:
    return str(
        _scalar(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            f"WHERE conrelid = 'audit.events'::regclass AND conname = '{_CHECK_NAME}'"
        )
    )


def _swap_outcome_check(vocab: tuple[str, ...]) -> None:
    values = ", ".join(f"'{v}'" for v in vocab)
    _exec(f"ALTER TABLE audit.events DROP CONSTRAINT {_CHECK_NAME}")
    _exec(f"ALTER TABLE audit.events ADD CONSTRAINT {_CHECK_NAME} CHECK (outcome IN ({values}))")


def upgrade() -> None:
    # 0. Target-safety guard before any DDL.
    _guard_target()

    # 1. prior_trace_id — existence-gated (the 0006 pattern), so a manifest-fresh
    #    database (0001 applied the updated events.sql) is untouched.
    if not _column_exists():
        _exec("ALTER TABLE audit.events ADD COLUMN prior_trace_id UUID NULL")
        _exec(
            "COMMENT ON COLUMN audit.events.prior_trace_id IS "
            "'The PRIOR delivery''s trace when this row records a duplicate/dedup hit "
            "(outcome DUPLICATE_NOOP / DUPLICATE_OVERWRITTEN, or the worker dedup no-op). "
            "Promoted from event_data JSONB by Slice 30c (the D42 revision: console "
            "queryability). NULL on non-duplicate rows.'"
        )

    # 2. The outcome CHECK — definition-gated: swap only the 4-value form, so the
    #    fresh path (file already declares 6 values) is a no-op and a drifted
    #    manifest cannot be silently self-healed.
    definition = _outcome_check_def()
    if "DUPLICATE_NOOP" not in definition:
        _swap_outcome_check(_NEW_VOCAB)


def downgrade() -> None:
    # Same target-safety guard before any destructive DDL.
    _guard_target()

    # REFUSE-LOUDLY (the 0005 precedent): restoring the 4-value CHECK over rows
    # carrying DUPLICATE_* outcomes would fail at ADD CONSTRAINT or corrupt the
    # vocabulary contract. The operator dispositions those rows first.
    violating = _scalar(
        "SELECT COUNT(*) FROM audit.events WHERE outcome IN ('DUPLICATE_NOOP', 'DUPLICATE_OVERWRITTEN')"
    )
    if int(str(violating)) > 0:
        raise RuntimeError(
            f"Refusing to downgrade 0008: {violating} audit.events row(s) carry a "
            "DUPLICATE_NOOP/DUPLICATE_OVERWRITTEN outcome, which the restored "
            "4-value CHECK forbids. Disposition (delete or rewrite) those rows "
            "first; this downgrade never silently drops or mutates audit rows."
        )

    definition = _outcome_check_def()
    if "DUPLICATE_NOOP" in definition:
        _swap_outcome_check(_OLD_VOCAB)
    if _column_exists():
        _exec("ALTER TABLE audit.events DROP COLUMN prior_trace_id")
