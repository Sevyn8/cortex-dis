"""ConsumerConfig slice-40a healthz toggle: PORT is conditional-required.

The required-env raise tests live in test_service_surface.py (AC11/AC12 home);
this module covers ONLY the 40a additions plus the LOCAL-UNCHANGED guarantee.
"""

from __future__ import annotations

import importlib

import pytest

import streaming_consumer.config as config_module
from dis_core.errors import DisError
from streaming_consumer.config import HEALTH_STALENESS_SECONDS, ConsumerConfig

_DIS_URL = "postgresql+psycopg://u:p@localhost:5433/ithina_dis_db"


def _set_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_URL", _DIS_URL)
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "ithina-bronze-raw")
    monkeypatch.delenv("RUN_HEALTH_SERVER", raising=False)
    monkeypatch.delenv("PORT", raising=False)


def test_resolves_from_env_without_new_required_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    # The LOCAL-UNCHANGED guarantee (slice 40a): today's exact env profile resolves
    # with NO new required vars — toggle defaults off, PORT never demanded.
    _set_all(monkeypatch)
    cfg = ConsumerConfig.from_env()
    assert cfg.postgres_url == _DIS_URL
    assert cfg.pubsub_project_id == "local-dis"
    assert cfg.bronze_bucket == "ithina-bronze-raw"
    assert cfg.run_health_server is False
    assert cfg.health_port is None


@pytest.mark.parametrize("truthy", ["true", "TRUE", "1"])
def test_toggle_on_with_port_resolves(monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
    _set_all(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", truthy)
    monkeypatch.setenv("PORT", "8080")
    cfg = ConsumerConfig.from_env()
    assert cfg.run_health_server is True
    assert cfg.health_port == 8080


def test_toggle_on_missing_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # PORT is conditional-required: demanded ONLY when the toggle is on.
    _set_all(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", "true")
    with pytest.raises(DisError, match="PORT"):
        ConsumerConfig.from_env()


def test_toggle_on_non_integer_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_all(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", "true")
    monkeypatch.setenv("PORT", "not-a-port")
    with pytest.raises(DisError, match="PORT"):
        ConsumerConfig.from_env()


def test_toggle_other_value_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_all(monkeypatch)
    monkeypatch.setenv("RUN_HEALTH_SERVER", "false")
    cfg = ConsumerConfig.from_env()
    assert cfg.run_health_server is False
    assert cfg.health_port is None


def test_staleness_threshold_is_the_contract_value() -> None:
    # Design C: 60s, sized above the worst expected loop iteration.
    assert HEALTH_STALENESS_SECONDS == 60.0


def test_ingress_ready_subscription_defaults_to_contract_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # No override -> the local create_topics.py name, so local dev is unchanged.
    monkeypatch.delenv("INGRESS_READY_SUBSCRIPTION", raising=False)
    reloaded = importlib.reload(config_module)
    assert reloaded.INGRESS_READY_SUBSCRIPTION == "streaming-consumer.ingress.ready"


def test_ingress_ready_subscription_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deployment (terraform, from the pubsub module output) points the subscribe at the
    # actually-provisioned short name; the constant resolves at import, hence reload.
    monkeypatch.setenv("INGRESS_READY_SUBSCRIPTION", "dis-ingress-ready-sub")
    try:
        reloaded = importlib.reload(config_module)
        assert reloaded.INGRESS_READY_SUBSCRIPTION == "dis-ingress-ready-sub"
    finally:
        monkeypatch.delenv("INGRESS_READY_SUBSCRIPTION", raising=False)
        importlib.reload(config_module)
