"""Runner control-flow branches that need no database (exit codes, empty-CM directive)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from mirror_sync_consumer.config import MirrorSyncConfig
from mirror_sync_consumer.pull import runner
from mirror_sync_consumer.pull.reader import CmStore, CmTenant

_CM = "postgresql+psycopg://u:p@localhost:5432/ithina_platform_db"
_DIS = "postgresql+psycopg://u:p@localhost:5433/ithina_dis_db"


class _DummyEngine:
    async def dispose(self) -> None:  # runner disposes both engines in finally
        return None


def test_exit_codes_are_distinct() -> None:
    codes = [
        runner.EXIT_OK,
        runner.EXIT_CONFIG,
        runner.EXIT_CM_READ,
        runner.EXIT_CM_UNREACHABLE,
        runner.EXIT_TARGET,
        runner.EXIT_WRITE,
    ]
    assert runner.EXIT_OK == 0
    assert len(set(codes)) == len(codes)


async def test_missing_config_exits_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CM_DB_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    assert await runner._run() == runner.EXIT_CONFIG


async def test_empty_customer_master_exits_clean_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Directive 5: zero tenants under a confirmed platform context is a valid empty
    # first-load — log and exit 0, never write.
    monkeypatch.setenv("CM_DB_URL", _CM)
    monkeypatch.setenv("POSTGRES_URL", _DIS)
    monkeypatch.setattr(runner, "create_cm_engine", lambda url: _DummyEngine())
    monkeypatch.setattr(runner, "create_rls_engine", lambda url: _DummyEngine())

    async def _empty(
        engine: Any, config: MirrorSyncConfig, *, trace_id: UUID
    ) -> tuple[list[CmTenant], list[CmStore]]:
        return [], []

    async def _must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("upsert_identity must not run when CM returns zero tenants")

    monkeypatch.setattr(runner, "read_customer_master", _empty)
    monkeypatch.setattr(runner, "upsert_identity", _must_not_run)

    assert await runner._run() == runner.EXIT_OK
