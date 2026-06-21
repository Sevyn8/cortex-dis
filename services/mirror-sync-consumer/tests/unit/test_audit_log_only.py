"""Criterion 6 (softened): run boundaries are LOGGED; no audit.events emission this slice."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import mirror_sync_consumer
from mirror_sync_consumer.config import MirrorSyncConfig
from mirror_sync_consumer.pull import runner
from mirror_sync_consumer.pull.reader import CmStore, CmTenant
from mirror_sync_consumer.sinks.postgres import SyncResult, TenantSyncCounts, UpsertCounts

_CM = "postgresql+psycopg://u:p@localhost:5432/ithina_platform_db"
_DIS = "postgresql+psycopg://u:p@localhost:5433/ithina_dis_db"


def test_service_emits_no_audit_table_rows() -> None:
    # Audit is log-only this slice: the service must not import dis-audit nor write audit.events.
    # (Docstrings may *mention* them to explain why they're absent — so we match real usage.)
    src = Path(mirror_sync_consumer.__file__).parent
    for path in src.rglob("*.py"):
        body = path.read_text().lower()
        assert "import dis_audit" not in body, f"{path} imports dis_audit"
        assert "from dis_audit" not in body, f"{path} imports from dis_audit"
        assert "insert into audit" not in body, f"{path} writes audit.events"


class _DummyEngine:
    async def dispose(self) -> None:
        return None


async def test_run_start_is_logged(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("CM_DB_URL", _CM)
    monkeypatch.setenv("POSTGRES_URL", _DIS)
    monkeypatch.setattr(runner, "create_cm_engine", lambda url: _DummyEngine())
    monkeypatch.setattr(runner, "create_rls_engine", lambda url: _DummyEngine())

    async def _empty(
        engine: Any, config: MirrorSyncConfig, *, trace_id: UUID
    ) -> tuple[list[CmTenant], list[CmStore]]:
        return [], []

    monkeypatch.setattr(runner, "read_customer_master", _empty)

    with caplog.at_level(logging.INFO, logger="mirror-sync-consumer"):
        assert await runner._run() == runner.EXIT_OK

    messages = [r.getMessage() for r in caplog.records]
    assert any("run start" in m for m in messages)


async def test_run_end_and_per_tenant_counts_are_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Criterion 6: run END and per-tenant counts are logged on a populated run (the empty
    # path returns before the complete line, so this is the populated-path assertion).
    monkeypatch.setenv("CM_DB_URL", _CM)
    monkeypatch.setenv("POSTGRES_URL", _DIS)
    monkeypatch.setattr(runner, "create_cm_engine", lambda url: _DummyEngine())
    monkeypatch.setattr(runner, "create_rls_engine", lambda url: _DummyEngine())

    _now = datetime(2024, 1, 1, tzinfo=UTC)
    tenant = CmTenant(
        tenant_id=UUID(int=1), name="Acme", status="ACTIVE", pc_created_at=_now, pc_updated_at=_now
    )

    async def _read(
        engine: Any, config: MirrorSyncConfig, *, trace_id: UUID
    ) -> tuple[list[CmTenant], list[CmStore]]:
        return [tenant], []

    result = SyncResult(
        per_tenant=[
            TenantSyncCounts(
                tenant_id=tenant.tenant_id,
                tenants=UpsertCounts(inserted=1),
                stores=UpsertCounts(),
            )
        ]
    )

    async def _upsert(*args: Any, **kwargs: Any) -> SyncResult:
        return result

    monkeypatch.setattr(runner, "read_customer_master", _read)
    monkeypatch.setattr(runner, "upsert_identity", _upsert)

    with caplog.at_level(logging.INFO, logger="mirror-sync-consumer"):
        assert await runner._run() == runner.EXIT_OK

    messages = [r.getMessage() for r in caplog.records]
    assert any("run complete" in m for m in messages)
    assert any("tenant synced" in m for m in messages)
