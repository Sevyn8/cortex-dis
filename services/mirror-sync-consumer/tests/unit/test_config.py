"""Config resolution: fail loud on a missing required DSN (code-quality rule 4)."""

from __future__ import annotations

import pytest

from dis_core.errors import MirrorSyncError
from mirror_sync_consumer.config import DEFAULT_CM_DB_NAME, DIS_DB_NAME, MirrorSyncConfig

_CM = "postgresql+psycopg://u:p@localhost:5432/ithina_platform_db"
_DIS = "postgresql+psycopg://u:p@localhost:5433/ithina_dis_db"


def test_missing_cm_db_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CM_DB_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", _DIS)
    with pytest.raises(MirrorSyncError):
        MirrorSyncConfig.from_env()


def test_missing_postgres_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CM_DB_URL", _CM)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(MirrorSyncError):
        MirrorSyncConfig.from_env()


def test_resolves_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CM_DB_URL", _CM)
    monkeypatch.setenv("POSTGRES_URL", _DIS)
    monkeypatch.delenv("CM_DB_NAME", raising=False)
    config = MirrorSyncConfig.from_env()
    assert config.cm_db_url == _CM
    assert config.dis_db_url == _DIS
    assert config.cm_db_name == DEFAULT_CM_DB_NAME
    assert config.dis_db_name == DIS_DB_NAME


def test_cm_db_name_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CM_DB_URL", _CM)
    monkeypatch.setenv("POSTGRES_URL", _DIS)
    monkeypatch.setenv("CM_DB_NAME", "cm_replica")
    assert MirrorSyncConfig.from_env().cm_db_name == "cm_replica"
