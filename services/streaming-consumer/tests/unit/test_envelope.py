"""ingress.ready envelope: typed parse, loud contract violations, and the drift guard.

The drift guard reconciles the Pydantic model against the committed contract file
both directions (the 9b/dis-audit reconcile pattern), so neither the model nor
the frozen contract can change without the other noticing (hard rule 10).

Also pins the transport consequence of a contract-reject: ``process_message``
acks an unparseable envelope pre-pipeline (terminal — redelivery is identical)
without ever touching the pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dis_core.errors import EventContractError
from streaming_consumer.clients.pubsub import process_message
from streaming_consumer.envelope import IngressReadyEvent, parse_ingress_ready

# services/streaming-consumer/tests/unit/ -> repo root / contracts/pubsub.
_CONTRACTS = Path(__file__).resolve().parents[3].parent / "contracts" / "pubsub"
_SCHEMA = json.loads((_CONTRACTS / "ingress.ready.schema.json").read_text())


def _good_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "trace_id": "019e9508-0000-7000-8000-000000000001",
        "tenant_id": "019e5e3c-b5d3-705f-9002-2451c4ca2626",
        "store_id": "019e5e3c-b62e-75e6-ad62-529127ae944a",
        "source_id": "sc_pos_v1",
        "template_id": "019e98c9-df80-7649-98cd-83fb6293777a",  # Slice 8 carry (D71)
        "bronze_ref": "019e9508-0000-7000-8000-000000000002",
        "gcs_uri": (
            "gs://ithina-bronze-raw/tenant/019e5e3c-b5d3-705f-9002-2451c4ca2626/"
            "source/sc_pos_v1/yyyy=2026/mm=06/dd=05/019e9508-0000-7000-8000-000000000001.csv"
        ),
        "received_ts": "2026-06-05T12:00:00+00:00",
        "tenant_display_code": "buc-ees",
        "store_code": "TX-101",
    }


def test_good_payload_parses() -> None:
    event = parse_ingress_ready(json.dumps(_good_payload()).encode())
    assert event.schema_version == 1
    assert event.source_id == "sc_pos_v1"
    # Consumed since Slice 8a (D71 closed): keys the active-mapping lookup —
    # test_service_surface pins the predicate; test_template_lookup proves it.
    assert str(event.template_id) == "019e98c9-df80-7649-98cd-83fb6293777a"
    assert event.replay is False  # absent -> the contract default
    assert event.received_ts.tzinfo is not None
    # Slice 16f backward-compat: a payload lacking delimiter (pre-16f / replayed)
    # parses as comma — the same behaviour as before this slice.
    assert event.delimiter == ","


def test_delimiter_parsed_when_present() -> None:
    # A 16f producer sets the detected separator; the consumer reads it verbatim.
    event = parse_ingress_ready(json.dumps(_good_payload() | {"delimiter": ";"}).encode())
    assert event.delimiter == ";"


@pytest.mark.parametrize(
    "mutation",
    [
        ("trace_id", None),  # required, removed
        ("tenant_id", "not-a-uuid"),
        ("schema_version", 2),  # const: 1
        ("source_id", ""),  # min length
        # Required since Slice 8; since 8a this reject IS the template_id-absent
        # policy (D71): contract-reject + terminal ack, BEFORE the template-keyed
        # lookup — no consumer fallback exists. Recovery is Slice 12 replay from
        # bronze (D73).
        ("template_id", None),
        ("template_id", "not-a-uuid"),
        ("bronze_ref", None),
    ],
)
def test_contract_violations_raise_loudly(mutation: tuple[str, object]) -> None:
    field, value = mutation
    payload = _good_payload()
    if value is None:
        payload.pop(field)
    else:
        payload[field] = value
    with pytest.raises(EventContractError):
        parse_ingress_ready(json.dumps(payload).encode())


def test_extra_field_forbidden() -> None:
    payload = _good_payload()
    payload["surprise"] = "x"  # additionalProperties: false -> extra='forbid'
    with pytest.raises(EventContractError):
        parse_ingress_ready(json.dumps(payload).encode())


def test_not_json_raises() -> None:
    with pytest.raises(EventContractError, match="not valid JSON"):
        parse_ingress_ready(b"\x00\x01")
    with pytest.raises(EventContractError, match="not a JSON object"):
        parse_ingress_ready(b"[1, 2]")


def test_contract_error_carries_identifier_context() -> None:
    payload = _good_payload()
    payload["bronze_ref"] = "nope"
    try:
        parse_ingress_ready(json.dumps(payload).encode())
    except EventContractError as exc:
        assert exc.tenant_id == payload["tenant_id"]  # identifiers, never payload
        assert exc.trace_id == payload["trace_id"]
    else:  # pragma: no cover
        pytest.fail("expected EventContractError")


# ---------------------------------------------------------------------------
# Drift guard: model field set == contract field set, both directions.
# ---------------------------------------------------------------------------


def test_model_fields_match_contract_properties_exactly() -> None:
    model_fields = set(IngressReadyEvent.model_fields)
    contract_fields = set(_SCHEMA["properties"])
    assert model_fields == contract_fields, (
        f"model-only: {model_fields - contract_fields}; contract-only: {contract_fields - model_fields}"
    )


def test_model_required_set_matches_contract_required() -> None:
    model_required = {name for name, info in IngressReadyEvent.model_fields.items() if info.is_required()}
    assert model_required == set(_SCHEMA["required"])


def test_contract_is_additional_properties_false() -> None:
    # The model's extra='forbid' is only correct while the contract says so.
    assert _SCHEMA["additionalProperties"] is False


# ---------------------------------------------------------------------------
# The transport consequence: an unparseable envelope acks pre-pipeline.
# ---------------------------------------------------------------------------


class _MustNotProcess:
    """A pipeline stand-in: any process() call means the reject branch leaked."""

    async def process(self, event: object) -> object:
        raise AssertionError("pipeline.process must NOT be called for an unparseable envelope")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"\x00\x89garbage not json\xff", id="not-json"),
        pytest.param(b"[1, 2, 3]", id="json-but-not-an-object"),
        pytest.param(b'{"schema_version": 1}', id="object-missing-required-fields"),
    ],
)
async def test_unparseable_envelope_acks_without_processing(payload: bytes) -> None:
    """Every contract-reject shape is terminal: the same bytes fail identically on
    redelivery, so the message acks pre-pipeline (clients/pubsub.py) — and the
    pipeline is provably untouched (the stub raises on any process() call), so no
    bronze fetch and no write can have happened."""
    decision = await process_message(_MustNotProcess(), payload)  # type: ignore[arg-type]
    assert decision == "ack"
