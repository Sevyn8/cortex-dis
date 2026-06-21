"""csv.received envelope (producer side): population + the both-directions drift guard.

The 9b pattern, pointed at the PRODUCER: the model is field-for-field the frozen
contract (hard rule 10), the wire form validates against the schema with the
same Draft 2020-12 + format validator the repo contract tests use, and absent
optional codes are OMITTED, never null-filled (D52).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from dis_ui_server.publisher import CsvReceivedEnvelope, build_csv_received

_CONTRACTS = Path(__file__).resolve().parents[3].parent / "contracts" / "pubsub"
_SCHEMA = json.loads((_CONTRACTS / "csv.received.schema.json").read_text())

_TENANT = UUID("019e5e3c-b5d3-705f-9002-2451c4ca2626")
_STORE = UUID("019e5e3c-b62e-75e6-ad62-529127ae944a")
_TEMPLATE = UUID("019e98c9-df80-7649-98cd-83fb6293777a")
_TRACE = UUID("019e9508-0000-7000-8000-000000000001")
_GCS_URI = f"gs://ithina-bronze-raw/tenant/{_TENANT}/source/sc_pos_v1/yyyy=2026/mm=06/dd=06/{_TRACE}.csv"


def _build(**overrides: object) -> CsvReceivedEnvelope:
    values: dict[str, object] = {
        "trace_id": _TRACE,
        "tenant_id": _TENANT,
        "store_id": _STORE,
        "source_id": "sc_pos_v1",
        "template_id": _TEMPLATE,
        "upload_session_id": "us_4f1d2b9c03ae",
        "gcs_uri": _GCS_URI,
        "received_ts": datetime(2026, 6, 6, 9, 30, tzinfo=UTC),
        "tenant_display_code": "buc-ees",
        "store_code": "TX-101",
    }
    values.update(overrides)
    return build_csv_received(**values)  # type: ignore[arg-type]


def test_wire_form_validates_against_frozen_contract() -> None:
    wire = json.loads(_build().to_bytes())
    Draft202012Validator.check_schema(_SCHEMA)
    Draft202012Validator(_SCHEMA, format_checker=FormatChecker()).validate(wire)
    assert wire["template_id"] == str(_TEMPLATE)  # the Slice 8 carry (D71)
    assert wire["schema_version"] == 1


def test_absent_codes_are_omitted_never_fabricated() -> None:
    wire = json.loads(_build(tenant_display_code=None, store_code=None).to_bytes())
    assert "tenant_display_code" not in wire
    assert "store_code" not in wire
    Draft202012Validator(_SCHEMA, format_checker=FormatChecker()).validate(wire)


def test_model_fields_match_contract_properties_exactly() -> None:
    model_fields = set(CsvReceivedEnvelope.model_fields)
    contract_fields = set(_SCHEMA["properties"])
    assert model_fields == contract_fields, (
        f"model-only: {model_fields - contract_fields}; contract-only: {contract_fields - model_fields}"
    )


def test_model_required_set_matches_contract_required() -> None:
    model_required = {name for name, info in CsvReceivedEnvelope.model_fields.items() if info.is_required()}
    # schema_version is contract-required but model-defaulted (const 1, always
    # emitted on the wire — asserted by the wire-form validation test above).
    assert model_required | {"schema_version"} == set(_SCHEMA["required"])


def test_contract_is_additional_properties_false() -> None:
    # The model's extra='forbid' is only correct while the contract says so.
    assert _SCHEMA["additionalProperties"] is False
