"""Config resolution: required values raise, never default (code-quality rule 4)."""

from __future__ import annotations

import importlib

import pytest

import dis_ui_server.config as config_module
from dis_core.errors import DisError
from dis_ui_server.config import (
    API_PREFIX,
    CSV_RECEIVED_TOPIC,
    CSV_UPLOAD_BODY_CEILING_BYTES,
    CSV_UPLOAD_MAX_FILE_BYTES,
    SERVICE_NAME,
    UiServerConfig,
)


def _set_all_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@localhost:5433/ithina_dis_db")
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "ithina-bronze-raw")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")


@pytest.mark.parametrize("missing", ["POSTGRES_URL", "GCS_BUCKET_BRONZE", "PUBSUB_PROJECT_ID"])
def test_missing_required_env_raises(missing: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Fail-fast at startup is the crashloop signal for MISCONFIGURATION — kept
    # strictly separate from the present-but-unreachable case (test_health.py),
    # which must NOT crash startup.
    _set_all_required(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(DisError, match=missing):
        UiServerConfig.from_env()


def test_present_required_env_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_all_required(monkeypatch)
    config = UiServerConfig.from_env()
    assert config.postgres_url.endswith("/ithina_dis_db")
    assert config.gcs_bucket_bronze == "ithina-bronze-raw"
    assert config.pubsub_project_id == "local-dis"


def test_service_constants_frozen() -> None:
    assert SERVICE_NAME == "dis-ui-server"
    assert API_PREFIX == "/api/v1"
    # Slice 8: the publish target defaults to the contract name (hard rule 10) and
    # the upload ceiling (the synchronous-streaming register entry's decision value).
    assert CSV_RECEIVED_TOPIC == "csv.received"
    assert CSV_UPLOAD_MAX_FILE_BYTES == 10 * 1024 * 1024
    assert CSV_UPLOAD_BODY_CEILING_BYTES > CSV_UPLOAD_MAX_FILE_BYTES


def test_csv_received_topic_defaults_to_contract_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # No override -> the contract literal, so local dev is byte-for-byte unchanged.
    monkeypatch.delenv("CSV_RECEIVED_TOPIC", raising=False)
    reloaded = importlib.reload(config_module)
    assert reloaded.CSV_RECEIVED_TOPIC == "csv.received"


def test_csv_received_topic_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deployment (terraform, from the pubsub module output) points the publish at the
    # actually-provisioned short name; the constant resolves at import, hence reload.
    monkeypatch.setenv("CSV_RECEIVED_TOPIC", "dis-csv-received")
    try:
        reloaded = importlib.reload(config_module)
        assert reloaded.CSV_RECEIVED_TOPIC == "dis-csv-received"
    finally:
        monkeypatch.delenv("CSV_RECEIVED_TOPIC", raising=False)
        importlib.reload(config_module)
