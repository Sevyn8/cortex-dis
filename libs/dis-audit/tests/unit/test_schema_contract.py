"""The drift-guard hardening's narrowing proof (Slice 30c) — pure, no DB.

The integration guard feeds :func:`diff_schema` the REAL information_schema
rows; here we prove the diff actually CATCHES the failure modes the old
name-set guard let through (the D45 silent-loss class): a type narrowing and a
nullability flip are reported, by synthesizing live-shaped rows from the
contract itself and tampering one axis at a time. No database is touched.
"""

from __future__ import annotations

from dis_audit.event import AuditEvent
from dis_audit.schema_contract import EXPECTED_COLUMNS, LiveColumnRow, diff_schema


def _rows_from_contract() -> list[LiveColumnRow]:
    """Live-shaped rows synthesized FROM the contract (a clean schema by construction)."""
    return [
        (name, spec.data_type, spec.is_nullable, spec.character_maximum_length)
        for name, spec in EXPECTED_COLUMNS.items()
    ]


def _tamper(rows: list[LiveColumnRow], column: str, **changes: object) -> list[LiveColumnRow]:
    out: list[LiveColumnRow] = []
    for name, dtype, nullable, max_len in rows:
        if name == column:
            dtype = str(changes.get("data_type", dtype))
            nullable = str(changes.get("is_nullable", nullable))
            if "character_maximum_length" in changes:
                raw = changes["character_maximum_length"]
                max_len = None if raw is None else int(str(raw))
        out.append((name, dtype, nullable, max_len))
    return out


def test_clean_schema_diffs_empty() -> None:
    assert diff_schema(_rows_from_contract()) == []


def test_contract_covers_24_columns_and_agrees_with_the_model() -> None:
    assert len(EXPECTED_COLUMNS) == 24  # +prior_trace_id (Slice 30c)
    assert AuditEvent.db_column_names() == set(EXPECTED_COLUMNS)


def test_type_narrowing_is_reported() -> None:
    # The exact failure mode the old name-set guard let through: failure_code
    # varchar(64) -> varchar(32) would truncate-fail INSERTs, silently swallowed.
    rows = _tamper(_rows_from_contract(), "failure_code", character_maximum_length=32)
    diffs = diff_schema(rows)
    assert any("failure_code" in d and "max length" in d for d in diffs), diffs


def test_type_change_is_reported() -> None:
    rows = _tamper(_rows_from_contract(), "mapping_version_id", data_type="integer")
    diffs = diff_schema(rows)
    assert any("mapping_version_id" in d and "type" in d for d in diffs), diffs


def test_nullability_flip_is_reported() -> None:
    # trace_id NOT NULL -> NULL would let correlation-less rows land silently.
    rows = _tamper(_rows_from_contract(), "trace_id", is_nullable="YES")
    diffs = diff_schema(rows)
    assert any("trace_id" in d and "is_nullable" in d for d in diffs), diffs


def test_missing_and_extra_columns_are_reported_both_directions() -> None:
    rows = [r for r in _rows_from_contract() if r[0] != "prior_trace_id"]
    rows.append(("surprise_column", "text", "YES", None))
    diffs = diff_schema(rows)
    assert any("prior_trace_id" in d and "MISSING live" in d for d in diffs), diffs
    assert any("surprise_column" in d and "NOT in the contract" in d for d in diffs), diffs
