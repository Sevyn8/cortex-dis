"""AuditEvent model invariants (no DB): derived event_date, UTC enforcement, INSERT shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from dis_audit.event import AuditEvent
from dis_audit.stages import EventScope, Outcome, Stage
from dis_core.ids import new_uuid7
from dis_core.timestamps import NaiveDatetimeError


def _event(**overrides: object) -> AuditEvent:
    base: dict[str, object] = {
        "event_timestamp": datetime(2026, 6, 3, 12, 0, tzinfo=UTC),
        "trace_id": new_uuid7(),
        "tenant_id": new_uuid7(),
        "service_name": "streaming-consumer",
        "stage": Stage.CANONICAL_WRITTEN,
        "event_scope": EventScope.INGRESS_EVENT,
        "outcome": Outcome.SUCCESS,
    }
    base.update(overrides)
    return AuditEvent(**base)


def test_event_date_is_derived_utc_date() -> None:
    # A non-UTC aware timestamp is normalised to UTC, and event_date is its UTC date —
    # exactly what the live ck_audit_events_event_date_matches CHECK requires.
    ist = timezone(timedelta(hours=5, minutes=30))
    ev = _event(event_timestamp=datetime(2026, 6, 4, 2, 0, tzinfo=ist))  # 2026-06-03 20:30 UTC
    assert ev.event_date is not None
    assert ev.event_date.isoformat() == "2026-06-03"


def test_caller_event_date_is_overridden_not_trusted() -> None:
    # Even if a caller passes event_date, the validator derives it (CHECK can't be violated).
    ev = _event(event_date=datetime(1999, 1, 1).date())
    assert ev.event_date is not None and ev.event_date.isoformat() == "2026-06-03"


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises((NaiveDatetimeError, ValidationError)):
        _event(event_timestamp=datetime(2026, 6, 3, 12, 0))  # naive


def test_required_fields_enforced() -> None:
    with pytest.raises(ValidationError):
        AuditEvent(event_timestamp=datetime(2026, 6, 3, tzinfo=UTC))  # type: ignore[call-arg]


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        _event(not_a_real_column="x")


def test_insert_params_omit_server_defaulted_columns() -> None:
    params = _event(event_data={"written_to_table": "store_sku_sale_events"}).to_insert_params()
    assert "id" not in params and "_loaded_at" not in params and "loaded_at" not in params
    assert len(params) == 22  # +prior_trace_id (Slice 30c, the D42 revision)
    # event_data is serialised to a JSON string (cast to JSONB in SQL).
    assert isinstance(params["event_data"], str)
    # Enums are sent as their string value.
    assert params["stage"] == "CANONICAL_WRITTEN"
    assert params["outcome"] == "SUCCESS"


def test_db_column_names_alias_aware() -> None:
    cols = AuditEvent.db_column_names()
    assert "_loaded_at" in cols and "loaded_at" not in cols
    assert len(cols) == 24  # +prior_trace_id (Slice 30c, the D42 revision)
