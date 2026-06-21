"""The :class:`AuditEvent` model — one ``audit.events`` row.

Hand-aligned to the **live** ``ithina_dis_db`` ``audit.events`` schema (23 columns),
not to D14 / architecture §8 / the DDL file / the BigQuery shape. The integration
drift guard (``tests/integration``) reconciles the field set against
``information_schema.columns`` both directions as the guard against drift.

Invariants baked in here so a malformed row never reaches the backend:

- ``event_timestamp`` is timezone-aware UTC (``dis_core.timestamps.ensure_utc``; a naive
  datetime raises ``NaiveDatetimeError``). DIS never handles naive datetimes.
- ``event_date`` is **derived** from ``event_timestamp`` (UTC date), never accepted from
  the caller, so the live CHECK ``event_date = (event_timestamp AT TIME ZONE 'UTC')::date``
  cannot be violated.
- ``trace_id`` is a required, caller-supplied field — never minted here (hard rule 4).
- ``tenant_id`` mirrors the nullable column for the drift guard, but the writer enforces
  the product rule that every DIS audit event carries a known tenant (``decisions.md`` D43).
- ``id`` and ``_loaded_at`` (exposed as ``loaded_at`` with its DB alias) are server-defaulted
  (``uuidv7()`` / ``now()``); they are model fields for the exact-set drift guard but are
  omitted from the INSERT so the DB stamps them (``id`` via the sanctioned server-side
  default, ``dis_core.ids`` docstring).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dis_audit.stages import EventScope, Outcome, Stage
from dis_core.timestamps import ensure_utc

# Columns the writer never sends — the DB stamps them from its own defaults.
_SERVER_DEFAULTED: frozenset[str] = frozenset({"id", "_loaded_at"})


class AuditEvent(BaseModel):
    """One ``audit.events`` row. Field set is reconciled against the live columns."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # ---- Surrogate key (server-defaulted) ----
    id: UUID | None = None

    # ---- Time ----
    event_timestamp: datetime
    event_date: date | None = None  # derived in the model validator; never caller-set

    # ---- Correlation / identity ----
    trace_id: UUID
    # The PRIOR delivery's trace on a duplicate/dedup row (Slice 30c, the D42
    # revision: promoted from event_data JSONB for console queryability).
    prior_trace_id: UUID | None = None
    tenant_id: UUID | None = None
    data_ingress_event_id: UUID | None = None

    # ---- Stage identification ----
    service_name: str = Field(max_length=64)
    service_version: str | None = Field(default=None, max_length=64)
    stage: Stage
    event_scope: EventScope
    outcome: Outcome

    # ---- Per-event metrics ----
    row_count: int | None = Field(default=None, ge=0)
    rows_succeeded: int | None = Field(default=None, ge=0)
    rows_failed: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    row_offset: int | None = None

    # ---- Mapping context (a value the caller supplies; no dis-mapping dependency) ----
    mapping_version_id: int | None = None

    # ---- Failure detail ----
    failure_code: str | None = Field(default=None, max_length=64)
    failure_message: str | None = Field(default=None, max_length=2048)

    # ---- Stage-specific structured context ----
    event_data: dict[str, Any] | None = None

    # ---- Caller context (receiver stages only) ----
    auth_principal: str | None = Field(default=None, max_length=256)
    client_ip: str | None = None

    # ---- DIS-managed (server-defaulted) ----
    loaded_at: datetime | None = Field(default=None, alias="_loaded_at")

    @field_validator("event_timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        # Raises NaiveDatetimeError for a naive datetime; normalises any aware zone to UTC.
        # (dis-core ships py.typed, so ensure_utc's datetime return is seen directly.)
        return ensure_utc(value)

    @model_validator(mode="after")
    def _derive_event_date(self) -> AuditEvent:
        # event_date is always the UTC date of event_timestamp — never caller-trusted, so the
        # live ck_audit_events_event_date_matches CHECK can never be violated.
        object.__setattr__(self, "event_date", ensure_utc(self.event_timestamp).date())
        return self

    @classmethod
    def db_column_names(cls) -> set[str]:
        """The live ``audit.events`` column names this model maps to (alias-aware).

        Used by the drift guard to assert an exact set match against the live schema.
        """
        return {info.alias or name for name, info in cls.model_fields.items()}

    def to_insert_params(self) -> dict[str, Any]:
        """Column -> value for the INSERT, omitting server-defaulted columns.

        Enums are sent as their string value; ``event_data`` is serialised to a JSON
        string (cast to JSONB in SQL, matching the seeder precedent). UUID / datetime /
        date objects are left for the psycopg adapter.
        """
        params: dict[str, Any] = {
            "event_timestamp": self.event_timestamp,
            "event_date": self.event_date,
            "trace_id": self.trace_id,
            "prior_trace_id": self.prior_trace_id,
            "tenant_id": self.tenant_id,
            "data_ingress_event_id": self.data_ingress_event_id,
            "service_name": self.service_name,
            "service_version": self.service_version,
            "stage": self.stage.value,
            "event_scope": self.event_scope.value,
            "outcome": self.outcome.value,
            "row_count": self.row_count,
            "rows_succeeded": self.rows_succeeded,
            "rows_failed": self.rows_failed,
            "duration_ms": self.duration_ms,
            "row_offset": self.row_offset,
            "mapping_version_id": self.mapping_version_id,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "event_data": None if self.event_data is None else json.dumps(self.event_data),
            "auth_principal": self.auth_principal,
            "client_ip": self.client_ip,
        }
        assert not (_SERVER_DEFAULTED & params.keys())  # id / _loaded_at are never sent
        return params
