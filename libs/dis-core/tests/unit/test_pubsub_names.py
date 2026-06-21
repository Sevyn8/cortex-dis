"""resolve_pubsub_name: env override wins, else the frozen-contract default."""

from __future__ import annotations

import pytest

from dis_core.errors import DisError
from dis_core.pubsub_names import resolve_pubsub_name

_ENV = "CSV_RECEIVED_TOPIC"


def test_unset_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Local dev sets no override -> the contract literal, byte-for-byte unchanged.
    monkeypatch.delenv(_ENV, raising=False)
    assert resolve_pubsub_name(_ENV, "csv.received") == "csv.received"


def test_set_returns_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deployment points the app at the actually-provisioned short name.
    monkeypatch.setenv(_ENV, "dis-csv-received")
    assert resolve_pubsub_name(_ENV, "csv.received") == "dis-csv-received"


def test_override_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "  dis-csv-received  ")
    assert resolve_pubsub_name(_ENV, "csv.received") == "dis-csv-received"


@pytest.mark.parametrize("blank", ["", "   "])
def test_set_but_empty_raises(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    # Rule 4 / the CORS precedent: set-but-empty is ambiguous, not a silent fallback.
    monkeypatch.setenv(_ENV, blank)
    with pytest.raises(DisError, match=_ENV):
        resolve_pubsub_name(_ENV, "csv.received")
