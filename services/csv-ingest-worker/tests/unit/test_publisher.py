"""ingress.ready envelope: frozen-contract population + drift guard (AC6) and the
emulator-or-ambient construction (slice 40a)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from csv_ingest_worker.envelope import parse_csv_received
from csv_ingest_worker.publisher import (
    IngressReadyEnvelope,
    PubsubPublisher,
    build_ingress_ready,
)

_CONTRACTS = Path(__file__).resolve().parents[3].parent / "contracts" / "pubsub"
_SCHEMA = json.loads((_CONTRACTS / "ingress.ready.schema.json").read_text())
_CSV_EXAMPLE = json.loads((_CONTRACTS / "csv.received.example.json").read_text())

_BRONZE_ID = UUID("019e93f0-57ca-7470-9899-ba6532ff15e1")
_RECEIVED_AT = datetime(2026, 6, 5, 11, 30, 12, tzinfo=UTC)


def _event_bytes(**overrides: object) -> bytes:
    payload = dict(_CSV_EXAMPLE)
    payload.update(overrides)
    return json.dumps(payload).encode()


def _build(*, delimiter: str = ",", **event_overrides: object) -> IngressReadyEnvelope:
    event = parse_csv_received(_event_bytes(**event_overrides))
    return build_ingress_ready(
        event,
        trace_id=event.trace_id,
        bronze_ref=_BRONZE_ID,
        received_at=_RECEIVED_AT,
        delimiter=delimiter,
    )


# ---------------------------------------------------------------------------
# Population: every required field, identity from the event, codes verbatim.
# ---------------------------------------------------------------------------


def test_populates_every_required_field_from_event_and_bronze() -> None:
    envelope = _build()
    assert envelope.schema_version == 1
    assert envelope.trace_id == UUID(_CSV_EXAMPLE["trace_id"])  # READ, never minted
    assert envelope.tenant_id == UUID(_CSV_EXAMPLE["tenant_id"])
    assert envelope.store_id == UUID(_CSV_EXAMPLE["store_id"])
    assert envelope.source_id == _CSV_EXAMPLE["source_id"]
    assert envelope.template_id == UUID(_CSV_EXAMPLE["template_id"])  # verbatim pass-through (D71)
    assert envelope.bronze_ref == _BRONZE_ID
    assert envelope.gcs_uri == _CSV_EXAMPLE["gcs_uri"]
    # received_ts is when DIS durably accepted (bronze received_at), NOT the
    # producer's csv.received.received_ts (the dual-received_ts note, D59).
    assert envelope.received_ts == _RECEIVED_AT
    assert envelope.replay is False
    assert envelope.parent_trace_id is None


def test_codes_propagated_verbatim() -> None:
    envelope = _build()
    assert envelope.tenant_display_code == _CSV_EXAMPLE["tenant_display_code"]
    assert envelope.store_code == _CSV_EXAMPLE["store_code"]


def test_absent_codes_are_omitted_never_fabricated() -> None:
    payload = dict(_CSV_EXAMPLE)
    del payload["tenant_display_code"]
    del payload["store_code"]
    event = parse_csv_received(json.dumps(payload).encode())
    envelope = build_ingress_ready(
        event,
        trace_id=event.trace_id,
        bronze_ref=_BRONZE_ID,
        received_at=_RECEIVED_AT,
        delimiter=",",
    )
    wire = json.loads(envelope.to_bytes())
    assert "tenant_display_code" not in wire
    assert "store_code" not in wire


def test_resume_path_publishes_under_the_passed_prior_trace() -> None:
    # D59: the resume branch publishes under the PRIOR ingest's trace_id, which the
    # builder takes explicitly — also a read trace, never minted.
    event = parse_csv_received(_event_bytes())
    prior_trace = UUID("019e0000-0000-7000-8000-00000000aaaa")
    envelope = build_ingress_ready(
        event,
        trace_id=prior_trace,
        bronze_ref=_BRONZE_ID,
        received_at=_RECEIVED_AT,
        delimiter=",",
    )
    assert envelope.trace_id == prior_trace
    # template_id comes off the INCOMING event, never a bronze read — by
    # construction the builder takes no bronze row, so a pre-Slice-8 prior row
    # with a NULL template_id column cannot wedge the resume publish.
    assert envelope.template_id == event.template_id


# ---------------------------------------------------------------------------
# The wire form validates against the FROZEN contract (Draft 2020-12 + formats),
# the same validator the repo contract tests use.
# ---------------------------------------------------------------------------


def test_wire_form_validates_against_frozen_contract() -> None:
    wire = json.loads(_build().to_bytes())
    Draft202012Validator.check_schema(_SCHEMA)
    Draft202012Validator(_SCHEMA, format_checker=FormatChecker()).validate(wire)


def test_wire_form_without_codes_still_validates() -> None:
    payload = dict(_CSV_EXAMPLE)
    del payload["tenant_display_code"]
    del payload["store_code"]
    event = parse_csv_received(json.dumps(payload).encode())
    envelope = build_ingress_ready(
        event,
        trace_id=event.trace_id,
        bronze_ref=_BRONZE_ID,
        received_at=_RECEIVED_AT,
        delimiter=",",
    )
    Draft202012Validator(_SCHEMA, format_checker=FormatChecker()).validate(json.loads(envelope.to_bytes()))


# ---------------------------------------------------------------------------
# Delimiter (Slice 16f): populated from the detected separator, on the wire,
# and a non-comma value validates against the frozen contract.
# ---------------------------------------------------------------------------


def test_delimiter_populated_and_on_the_wire() -> None:
    envelope = _build(delimiter=";")
    assert envelope.delimiter == ";"
    wire = json.loads(envelope.to_bytes())
    assert wire["delimiter"] == ";"  # default-non-None, so never omitted by exclude_none


def test_default_delimiter_is_comma() -> None:
    assert _build().delimiter == ","


@pytest.mark.parametrize("sep", [",", ";", "\t", "|"])
def test_wire_form_with_each_delimiter_validates_against_frozen_contract(sep: str) -> None:
    wire = json.loads(_build(delimiter=sep).to_bytes())
    Draft202012Validator(_SCHEMA, format_checker=FormatChecker()).validate(wire)
    assert wire["delimiter"] == sep


# ---------------------------------------------------------------------------
# Drift guard: model field set == contract field set, both directions.
# ---------------------------------------------------------------------------


def test_model_fields_match_contract_properties_exactly() -> None:
    model_fields = set(IngressReadyEnvelope.model_fields)
    contract_fields = set(_SCHEMA["properties"])
    assert model_fields == contract_fields, (
        f"model-only: {model_fields - contract_fields}; contract-only: {contract_fields - model_fields}"
    )


def test_model_required_set_matches_contract_required() -> None:
    model_required = {name for name, info in IngressReadyEnvelope.model_fields.items() if info.is_required()}
    # schema_version is contract-required but model-defaulted (const 1, always
    # emitted on the wire — asserted by the wire-form validation test above).
    assert model_required | {"schema_version"} == set(_SCHEMA["required"])


def test_contract_is_additional_properties_false() -> None:
    assert _SCHEMA["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Runtime publisher is emulator-or-ambient (slice 40a): no-emulator constructs.
# ---------------------------------------------------------------------------


class _RecordingPublisherClient:
    """Stands in for pubsub_v1.PublisherClient; records construction kwargs."""

    last: _RecordingPublisherClient | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        _RecordingPublisherClient.last = self


def test_publisher_constructs_without_emulator_var_ambient_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The branch that pre-40a raised now constructs a bare client (ambient ADC =
    # pass nothing; pubsub_v1 honours PUBSUB_EMULATOR_HOST natively, so both
    # branches are the identical no-kwargs construction).
    from google.cloud import pubsub_v1

    monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)
    monkeypatch.setattr(pubsub_v1, "PublisherClient", _RecordingPublisherClient)
    PubsubPublisher(project_id="real-project")
    client = _RecordingPublisherClient.last
    assert client is not None
    assert client.args == ()
    assert client.kwargs == {}
