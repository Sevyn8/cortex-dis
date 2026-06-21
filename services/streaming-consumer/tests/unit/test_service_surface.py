"""AC1/AC11/AC12 units: importable surface, required config, error and contract
discipline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dis_core.errors import DisError
from streaming_consumer.config import (
    BATCH_SIZE_ROW_PAIRS,
    INGRESS_READY_SUBSCRIPTION,
    INGRESS_READY_TOPIC,
    ConsumerConfig,
)

_REPO = Path(__file__).resolve().parents[3].parent
_SRC = Path(__file__).resolve().parents[2] / "src" / "streaming_consumer"


def test_importable_surface() -> None:
    # AC1: the service is importable as a package (collection is proven by the
    # suite itself running under the repo testpaths).
    import streaming_consumer.main
    import streaming_consumer.orchestrate

    assert callable(streaming_consumer.main.main)
    assert hasattr(streaming_consumer.orchestrate, "ConsumerPipeline")


def test_frozen_constants() -> None:
    assert INGRESS_READY_TOPIC == "ingress.ready"  # hard rule 10
    assert INGRESS_READY_SUBSCRIPTION == "streaming-consumer.ingress.ready"
    assert BATCH_SIZE_ROW_PAIRS == 500  # architecture 4.6 grain


@pytest.mark.parametrize("missing", ["POSTGRES_URL", "PUBSUB_PROJECT_ID", "GCS_BUCKET_BRONZE"])
def test_required_env_raises_loudly(missing: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Code-quality rule 4: no silent default for a required value.
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@localhost:5433/ithina_dis_db")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "bucket")
    monkeypatch.delenv(missing)
    with pytest.raises(DisError, match=missing):
        ConsumerConfig.from_env()


def test_no_raw_runtime_or_value_errors_raised() -> None:
    # AC12: the service raises dis-core errors. The one sanctioned exception is
    # normalize.py's defensive TypeError on a post-validation impossibility.
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if re.search(r"raise (RuntimeError|ValueError)\(", line):
                offenders.append(f"{path.name}:{line_number}")
    assert offenders == []


def test_mapping_lookup_is_template_keyed() -> None:
    # Slice 8a (D71 closed): the INVERSE of the retired Slice 8 pin
    # (test_mapping_lookup_stays_template_unaware_until_slice_8a). The
    # active-mapping lookup now keys on (tenant, source, template); the live
    # uq_csm_active_per_source index — (tenant_id, source_id, template_id)
    # WHERE status='ACTIVE' — then guarantees at most one row, so a second
    # ACTIVE template under one source resolves exactly, never by .first()
    # luck. The behavioural proof is the two-ACTIVE-templates integration test
    # (tests/integration/test_template_lookup.py); this pin guards the
    # predicate's presence in the source.
    mapping_source = (_SRC / "pipeline" / "mapping.py").read_text()
    assert "AND template_id = CAST(:template_id AS uuid)" in mapping_source, (
        "pipeline/mapping.py lost the template_id predicate — the lookup must "
        "stay template-keyed (Slice 8a, D71)"
    )


def test_contracts_describe_no_ordering_key() -> None:
    # AC11 (D60 resolved as STRIKE): neither contract mentions an ordering key —
    # this is the regression guard on the strike.
    for name in ("ingress.ready.schema.json", "csv.received.schema.json"):
        schema_text = (_REPO / "contracts" / "pubsub" / name).read_text()
        assert "ordering key" not in schema_text.lower(), name
        # The strike was description-only: the schema still parses and the
        # tenant_id property survives intact.
        schema = json.loads(schema_text)
        assert "tenant_id" in schema["properties"]
