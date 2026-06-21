"""UTC-only timestamp helpers. DIS never handles naive datetimes.

Every timestamp in the system is timezone-aware UTC. Canonical ``timestamptz``
columns, audit event times, and ``received_ts``/``event_ts`` all assume UTC. A
naive ``datetime`` is a bug: it silently adopts whatever the host's local zone is.
These helpers are the only sanctioned way to obtain "now" and to normalise an
incoming datetime to UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dis_core.errors import DisError


class NaiveDatetimeError(DisError):
    """A naive (tz-unaware) datetime was passed where an aware UTC one is required."""


def now_utc() -> datetime:
    """Return the current time as a timezone-aware UTC :class:`datetime`."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` converted to UTC.

    Raises :class:`NaiveDatetimeError` if ``dt`` is naive — we refuse to guess a
    zone. An aware datetime in any zone is converted to UTC.
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise NaiveDatetimeError("naive datetime is not allowed; pass a tz-aware value")
    return dt.astimezone(UTC)
