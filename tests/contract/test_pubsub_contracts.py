"""Pub/Sub contract verification (Slice 9a AC6) — the instrument for the D52 hand edits.

Two assertions over the frozen contracts in ``contracts/pubsub/``:

  1. Every example validates against its schema (Draft 2020-12 + format checks),
     for all SEVEN envelopes including ``csv.received``.
  2. No identity field anywhere in the contract files retains the retired invented
     ``t_*``/``s_*`` form (D52) — a scope assertion over the raw file text.

Errors, never skips: the envelope list is pinned literally (no glob), so a missing
schema or example file is a loud failure, not a silently smaller test run
(the Slice 4/7 load-bearing-proof rule).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

# Resolved from tests/contract/ -> repo root (parents[2]) / contracts/pubsub.
_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "pubsub"

# Pinned literally — adding an envelope is a deliberate edit here, and a missing
# file errors instead of shrinking coverage.
ENVELOPES = (
    "csv.received",
    "identity.changed",
    "ingress.ready",
    "ingress.resubmit",
    "mapping.changed",
    "pipeline.dlq",
    "quarantine",
)

# The retired invented identity form (D52): t_/s_ + 12 lowercase alphanumerics.
# Lookbehind so legitimate forms never false-positive — e.g. the 's_acme9k2l1mn4'
# substring inside the upload-session id 'us_acme9k2l1mn4'.
_RETIRED_FORM = re.compile(r"(?<![a-z0-9_])[ts]_[a-z0-9]{12}")


def _read(name: str, kind: str) -> str:
    path = _CONTRACTS / f"{name}.{kind}.json"
    # .read_text() raises FileNotFoundError on absence — error, never skip.
    return path.read_text()


def test_contract_dir_holds_exactly_the_pinned_envelopes() -> None:
    # Both directions: every pinned file exists (errors below otherwise), and no
    # unpinned contract rides along unverified.
    present = {p.name.removesuffix(".schema.json") for p in _CONTRACTS.glob("*.schema.json")}
    assert present == set(ENVELOPES)


@pytest.mark.parametrize("name", ENVELOPES)
def test_example_validates_against_schema(name: str) -> None:
    schema = json.loads(_read(name, "schema"))
    example = json.loads(_read(name, "example"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)


@pytest.mark.parametrize("name", ENVELOPES)
@pytest.mark.parametrize("kind", ["schema", "example"])
def test_no_identity_field_retains_the_retired_form(name: str, kind: str) -> None:
    content = _read(name, kind)
    hits = _RETIRED_FORM.findall(content)
    assert not hits, f"{name}.{kind}.json still carries the retired t_*/s_* form: {hits}"


@pytest.mark.parametrize("name", ENVELOPES)
def test_identity_fields_are_uuid_typed(name: str) -> None:
    # The positive half of the D52 assertion: every tenant_id/store_id/entity_id
    # property the schema declares is format: uuid.
    schema = json.loads(_read(name, "schema"))
    for field in ("tenant_id", "store_id", "entity_id"):
        prop = schema.get("properties", {}).get(field)
        if prop is not None:
            assert prop.get("format") == "uuid", f"{name}.schema.json {field} is not format: uuid"
