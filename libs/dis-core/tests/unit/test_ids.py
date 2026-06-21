"""Unit tests for the UUIDv7 generation helper."""

from __future__ import annotations

from uuid import UUID

from dis_core.ids import new_uuid7


def test_new_uuid7_is_stdlib_uuid_version_7() -> None:
    value = new_uuid7()
    assert isinstance(value, UUID)
    assert value.version == 7


def test_new_uuid7_is_unique_and_time_ordered() -> None:
    first = new_uuid7()
    second = new_uuid7()
    assert first != second
    # UUIDv7 is time-ordered: a later mint sorts at or after an earlier one.
    assert second >= first
