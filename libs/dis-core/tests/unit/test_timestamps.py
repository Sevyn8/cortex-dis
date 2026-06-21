"""Unit tests for the UTC-only timestamp helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from dis_core.timestamps import NaiveDatetimeError, ensure_utc, now_utc


def test_now_utc_is_aware_and_utc() -> None:
    now = now_utc()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_ensure_utc_converts_other_zone() -> None:
    plus_five = timezone(timedelta(hours=5, minutes=30))
    dt = datetime(2026, 6, 3, 18, 0, tzinfo=plus_five)
    converted = ensure_utc(dt)
    assert converted.utcoffset() == timedelta(0)
    assert converted == datetime(2026, 6, 3, 12, 30, tzinfo=UTC)


def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(NaiveDatetimeError):
        ensure_utc(datetime(2026, 6, 3, 12, 0))
