"""WorkerConfig: required env resolves; a missing required value raises (rule 4)."""

from __future__ import annotations

import importlib

import pytest

import csv_ingest_worker.config as config_module
from csv_ingest_worker.config import (
    CSV_RECEIVED_SUBSCRIPTION,
    CSV_RECEIVED_TOPIC,
    DEDUP_WINDOW_HOURS,
    INGRESS_READY_TOPIC,
    WorkerConfig,
)
from dis_core.errors import CsvIngestError, DisError

_DIS_URL = "postgresql+psycopg://u:p@localhost:5433/ithina_dis_db"


def _set_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_URL", _DIS_URL)
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "ithina-bronze-raw")
    monkeypatch.delenv("RUN_HEALTH_SERVER", raising=False)
    monkeypatch.delenv("PORT", raising=False)


def test_resolves_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The LOCAL-UNCHANGED guarantee (slice 40a): today's exact env profile resolves
    # with NO new required vars — toggle defaults off, PORT never demanded.
    _set_all(monkeypatch)
    cfg = WorkerConfig.from_env()
    assert cfg.postgres_url == _DIS_URL
    assert cfg.pubsub_project_id == "local-dis"
    assert cfg.bronze_bucket == "ithina-bronze-raw"
    assert cfg.run_health_server is False
    assert cfg.health_port is None


@pytest.mark.parametrize("missing", ["POSTGRES_URL", "PUBSUB_PROJECT_ID", "GCS_BUCKET_BRONZE"])
def test_missing_required_value_raises_dis_error(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    # No silent fallback for a required value (code-quality rule 4); the error is
    # DisError-rooted and names the missing variable.
    _set_all(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(CsvIngestError, match=missing):
        WorkerConfig.from_env()
    assert issubclass(CsvIngestError, DisError)


@pytest.mark.parametrize("empty", ["POSTGRES_URL", "PUBSUB_PROJECT_ID", "GCS_BUCKET_BRONZE"])
def test_empty_required_value_raises(monkeypatch: pytest.MonkeyPatch, empty: str) -> None:
    _set_all(monkeypatch)
    monkeypatch.setenv(empty, "")
    with pytest.raises(CsvIngestError, match=empty):
        WorkerConfig.from_env()


# -- the slice-40a healthz toggle ---------------------------------------------------


@pytest.mark.parametrize("truthy", ["true", "TRUE", "1"])
def test_toggle_on_with_port_resolves(monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
    _set_all(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", truthy)
    monkeypatch.setenv("PORT", "8080")
    cfg = WorkerConfig.from_env()
    assert cfg.run_health_server is True
    assert cfg.health_port == 8080


def test_toggle_on_missing_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # PORT is conditional-required: demanded ONLY when the toggle is on.
    _set_all(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", "true")
    with pytest.raises(CsvIngestError, match="PORT"):
        WorkerConfig.from_env()


def test_toggle_on_non_integer_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_all(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", "true")
    monkeypatch.setenv("PORT", "not-a-port")
    with pytest.raises(CsvIngestError, match="PORT"):
        WorkerConfig.from_env()


def test_toggle_other_value_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_all(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", "false")
    cfg = WorkerConfig.from_env()
    assert cfg.run_health_server is False
    assert cfg.health_port is None


def test_contract_names_default_to_frozen_literals() -> None:
    # Defaults are the contract names (hard rule 10), so local dev is unchanged.
    # CSV_RECEIVED_TOPIC is NOT env-resolved (the worker subscribes, never publishes it).
    assert CSV_RECEIVED_TOPIC == "csv.received"
    assert INGRESS_READY_TOPIC == "ingress.ready"
    assert CSV_RECEIVED_SUBSCRIPTION == "csv-ingest-worker.csv.received"
    assert DEDUP_WINDOW_HOURS == 24


def test_pubsub_names_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGRESS_READY_TOPIC", raising=False)
    monkeypatch.delenv("CSV_RECEIVED_SUBSCRIPTION", raising=False)
    reloaded = importlib.reload(config_module)
    assert reloaded.INGRESS_READY_TOPIC == "ingress.ready"
    assert reloaded.CSV_RECEIVED_SUBSCRIPTION == "csv-ingest-worker.csv.received"


def test_pubsub_names_honour_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deployment (terraform, from the pubsub module output) points the publish/subscribe
    # at the actually-provisioned short names; constants resolve at import, hence reload.
    monkeypatch.setenv("INGRESS_READY_TOPIC", "dis-ingress-ready")
    monkeypatch.setenv("CSV_RECEIVED_SUBSCRIPTION", "dis-csv-received-sub")
    try:
        reloaded = importlib.reload(config_module)
        assert reloaded.INGRESS_READY_TOPIC == "dis-ingress-ready"
        assert reloaded.CSV_RECEIVED_SUBSCRIPTION == "dis-csv-received-sub"
    finally:
        monkeypatch.delenv("INGRESS_READY_TOPIC", raising=False)
        monkeypatch.delenv("CSV_RECEIVED_SUBSCRIPTION", raising=False)
        importlib.reload(config_module)
