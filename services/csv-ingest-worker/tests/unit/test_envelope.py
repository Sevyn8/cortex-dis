"""csv.received envelope: typed parse, loud contract violations, and the drift guard.

The drift guard reconciles the Pydantic model against the committed contract file
both directions (the dis-audit live-schema reconcile pattern), so neither the model
nor the frozen contract can change without the other noticing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from csv_ingest_worker.envelope import CsvReceivedEvent, parse_csv_received
from dis_core.errors import DisError, EventContractError

# services/csv-ingest-worker/tests/unit/ -> repo root (parents[3]) / contracts/pubsub.
_CONTRACTS = Path(__file__).resolve().parents[3].parent / "contracts" / "pubsub"
_SCHEMA = json.loads((_CONTRACTS / "csv.received.schema.json").read_text())
_EXAMPLE = json.loads((_CONTRACTS / "csv.received.example.json").read_text())


def _example(**overrides: Any) -> dict[str, Any]:
    payload = dict(_EXAMPLE)
    payload.update(overrides)
    return payload


def _as_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# Happy path: the committed contract example parses as-is.
# ---------------------------------------------------------------------------


def test_contract_example_parses() -> None:
    event = parse_csv_received(_as_bytes(_EXAMPLE))
    assert event.schema_version == 1
    assert event.trace_id == UUID(_EXAMPLE["trace_id"])
    assert event.tenant_id == UUID(_EXAMPLE["tenant_id"])
    assert event.store_id == UUID(_EXAMPLE["store_id"])
    assert event.source_id == _EXAMPLE["source_id"]
    assert event.template_id == UUID(_EXAMPLE["template_id"])  # Slice 8 carry (D71)
    assert event.upload_session_id == _EXAMPLE["upload_session_id"]
    assert event.gcs_uri == _EXAMPLE["gcs_uri"]
    assert event.tenant_display_code == _EXAMPLE["tenant_display_code"]
    assert event.store_code == _EXAMPLE["store_code"]
    assert event.received_ts.tzinfo is not None  # aware, normalised UTC


def test_optional_codes_may_be_absent() -> None:
    # Optional in the schema (producer-required is the PRODUCER's obligation, D52);
    # the worker must still consume an envelope without them.
    payload = _example()
    del payload["tenant_display_code"]
    del payload["store_code"]
    event = parse_csv_received(_as_bytes(payload))
    assert event.tenant_display_code is None
    assert event.store_code is None


# ---------------------------------------------------------------------------
# Contract violations: loud, typed, field-named (rule 4 / rule 5).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(_SCHEMA["required"]))
def test_missing_required_field_raises_with_field_name(field: str) -> None:
    payload = _example()
    del payload[field]
    with pytest.raises(EventContractError) as exc_info:
        parse_csv_received(_as_bytes(payload))
    assert field in str(exc_info.value)
    assert issubclass(EventContractError, DisError)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("trace_id", "not-a-uuid"),
        ("tenant_id", "t_acme9k2l1mn4"),  # the retired invented form
        ("store_id", ""),
        ("template_id", "not-a-uuid"),  # Slice 8 carry: required, format uuid
        ("schema_version", 2),  # const: 1
        ("upload_session_id", "sess-12345"),  # violates ^us_[a-z0-9]{12}$
        ("upload_session_id", ""),  # the idempotency key is a required value
        ("source_id", ""),
        ("received_ts", "not-a-timestamp"),
    ],
)
def test_malformed_field_raises_with_field_name(field: str, bad: Any) -> None:
    with pytest.raises(EventContractError) as exc_info:
        parse_csv_received(_as_bytes(_example(**{field: bad})))
    assert exc_info.value.field == field


def test_unknown_field_rejected() -> None:
    # additionalProperties: false in the contract -> extra='forbid' in the model.
    with pytest.raises(EventContractError):
        parse_csv_received(_as_bytes(_example(surprise="field")))


def test_non_json_body_raises() -> None:
    with pytest.raises(EventContractError, match="not valid JSON"):
        parse_csv_received(b"\x00\x01 not json")


def test_non_object_body_raises() -> None:
    with pytest.raises(EventContractError, match="not a JSON object"):
        parse_csv_received(b'["a", "list"]')


def test_contract_error_carries_identifier_context() -> None:
    # When the malformed payload still exposes the identifiers, the error carries
    # them (code-quality rule 5) — identifiers only, never the payload.
    payload = _example()
    del payload["gcs_uri"]
    with pytest.raises(EventContractError) as exc_info:
        parse_csv_received(_as_bytes(payload))
    assert exc_info.value.tenant_id == _EXAMPLE["tenant_id"]
    assert exc_info.value.trace_id == _EXAMPLE["trace_id"]


# ---------------------------------------------------------------------------
# Drift guard: model field set == contract field set, both directions.
# ---------------------------------------------------------------------------


def test_model_fields_match_contract_properties_exactly() -> None:
    model_fields = set(CsvReceivedEvent.model_fields)
    contract_fields = set(_SCHEMA["properties"])
    assert model_fields == contract_fields, (
        f"model-only: {model_fields - contract_fields}; contract-only: {contract_fields - model_fields}"
    )


def test_model_required_set_matches_contract_required() -> None:
    model_required = {name for name, info in CsvReceivedEvent.model_fields.items() if info.is_required()}
    assert model_required == set(_SCHEMA["required"])


def test_contract_is_additional_properties_false() -> None:
    # The model's extra='forbid' is only correct while the contract says so.
    assert _SCHEMA["additionalProperties"] is False
