"""The frozen audit.events schema contract + the fail-loud drift diff (Slice 30c).

Before this slice the model-vs-schema drift guard checked the column-NAME set
only (both directions). A type narrowing (``varchar(64)`` → ``varchar(32)``) or
a nullability flip passed that guard and surfaced only as a runtime INSERT
failure — which the fire-and-forget writer swallows. That is the same
silent-loss class as the D45 partition cliff: the audit trail stops recording
and nothing fails loud.

This module is the hardening: :data:`EXPECTED_COLUMNS` freezes the full
per-column shape (data_type, is_nullable, character_maximum_length, straight
from ``information_schema.columns`` vocabulary), and :func:`diff_schema` is a
PURE comparison — the integration drift guard feeds it the live introspection
rows; unit tests prove a synthetic narrowing and a nullability flip are
reported WITHOUT touching the database.

Three-way tie: the model's ``AuditEvent.db_column_names()`` must equal this
contract's key set (a unit pin), and the live schema must diff clean against
the contract (the integration guard) — so model ↔ contract ↔ live agree
transitively, now at type/nullability grain, not just names.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnSpec:
    """One column's live shape, in ``information_schema.columns`` vocabulary."""

    data_type: str
    is_nullable: str  # 'YES' | 'NO' — kept verbatim so live rows compare directly
    character_maximum_length: int | None = None


# The live audit.events shape, post-0008 (24 columns). Ordinal positions are
# deliberately NOT part of the contract: the 0008 ALTER appends prior_trace_id
# on migrated databases while the DDL file places it beside trace_id — the
# fresh-vs-migrate equality is name-keyed, and the writer INSERT names columns.
EXPECTED_COLUMNS: dict[str, ColumnSpec] = {
    "id": ColumnSpec("uuid", "NO"),
    "event_timestamp": ColumnSpec("timestamp with time zone", "NO"),
    "event_date": ColumnSpec("date", "NO"),
    "trace_id": ColumnSpec("uuid", "NO"),
    "prior_trace_id": ColumnSpec("uuid", "YES"),  # Slice 30c (the D42 revision)
    "tenant_id": ColumnSpec("uuid", "YES"),
    "data_ingress_event_id": ColumnSpec("uuid", "YES"),
    "service_name": ColumnSpec("character varying", "NO", 64),
    "service_version": ColumnSpec("character varying", "YES", 64),
    "stage": ColumnSpec("character varying", "NO", 64),
    "event_scope": ColumnSpec("character varying", "NO", 32),
    "outcome": ColumnSpec("character varying", "NO", 32),
    "row_count": ColumnSpec("integer", "YES"),
    "rows_succeeded": ColumnSpec("integer", "YES"),
    "rows_failed": ColumnSpec("integer", "YES"),
    "duration_ms": ColumnSpec("integer", "YES"),
    "row_offset": ColumnSpec("integer", "YES"),
    "mapping_version_id": ColumnSpec("bigint", "YES"),
    "failure_code": ColumnSpec("character varying", "YES", 64),
    "failure_message": ColumnSpec("character varying", "YES", 2048),
    "event_data": ColumnSpec("jsonb", "YES"),
    "auth_principal": ColumnSpec("character varying", "YES", 256),
    "client_ip": ColumnSpec("inet", "YES"),
    "_loaded_at": ColumnSpec("timestamp with time zone", "NO"),
}

# One live introspection row: (column_name, data_type, is_nullable, char_max_len).
LiveColumnRow = tuple[str, str, str, int | None]


def diff_schema(
    live_rows: list[LiveColumnRow],
    expected: dict[str, ColumnSpec] = EXPECTED_COLUMNS,
) -> list[str]:
    """Every disagreement between the live shape and the contract, human-readable.

    Pure: callers feed introspection rows (the integration guard) or synthetic
    rows (the narrowing/nullability unit proofs). Empty list == no drift. Checks
    the name set BOTH directions plus per-column type, nullability, and
    character length — the three axes a silent INSERT failure can hide behind.
    """
    diffs: list[str] = []
    live = {name: ColumnSpec(dtype, nullable, max_len) for name, dtype, nullable, max_len in live_rows}

    for name in sorted(expected.keys() - live.keys()):
        diffs.append(f"column {name!r}: in the contract but MISSING live")
    for name in sorted(live.keys() - expected.keys()):
        diffs.append(f"column {name!r}: live but NOT in the contract")
    for name in sorted(expected.keys() & live.keys()):
        want, got = expected[name], live[name]
        if got.data_type != want.data_type:
            diffs.append(f"column {name!r}: type {got.data_type!r} != expected {want.data_type!r}")
        if got.is_nullable != want.is_nullable:
            diffs.append(f"column {name!r}: is_nullable {got.is_nullable!r} != expected {want.is_nullable!r}")
        if got.character_maximum_length != want.character_maximum_length:
            diffs.append(
                f"column {name!r}: max length {got.character_maximum_length!r} != "
                f"expected {want.character_maximum_length!r} (a narrowing fails INSERTs "
                "silently under fire-and-forget — fix the contract or the schema)"
            )
    return diffs
