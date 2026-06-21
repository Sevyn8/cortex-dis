"""Fixtures for the Slice 9b worker integration tests.

These tests WRITE the DIS database and use the Pub/Sub + GCS emulators, so — the
Slice 4/7 lesson — they must NOT skip silently when the stack is absent: a missing
env or unreachable emulator is a loud ERROR (``StackRequiredError``), never a skip.
Everything runs against ``ithina_dis_db`` on 5433; Customer Master (5432) is never
touched.

Each test mints a UNIQUE ``upload_session_id``/``trace_id`` pair (the test plays
dis-ui-server, the Phase-1 producer — the WORKER under test still only reads them)
so the 24h dedup window cannot couple test runs; created bronze/audit rows are
deleted in teardown via the admin engine.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from dis_storage.client import StorageClient


class StackRequiredError(RuntimeError):
    """The local stack is required for these load-bearing tests but is absent."""


_REQUIRED_ENV = (
    "POSTGRES_URL",
    "POSTGRES_ADMIN_URL",
    "PUBSUB_EMULATOR_HOST",
    "STORAGE_EMULATOR_HOST",
    "GCS_BUCKET_BRONZE",
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise StackRequiredError(
            f"{name} is not set — the Slice 9b worker integration tests refuse to skip "
            "silently. Bring up the stack (make run-local) and load .env."
        )
    return value


@pytest.fixture(scope="session")
def stack_env() -> dict[str, str]:
    return {name: _require_env(name) for name in _REQUIRED_ENV}


@pytest.fixture(scope="session")
def seeded(stack_env: dict[str, str]) -> None:
    """Sync identity_mirror (mirror-sync owns it) then seed the default mapping.

    identity_mirror is owned by mirror-sync; the seeder only writes
    config.source_mappings and requires the tenant FK target to already exist,
    so the sync MUST run first — this is what makes the bronze identity FKs
    resolve.
    """
    from dis_testing.identity_sync import sync_identity_mirror
    from dis_testing.seed import seed_default_fixtures

    sync_identity_mirror(stack_env["POSTGRES_ADMIN_URL"], stack_env["POSTGRES_URL"])
    seed_default_fixtures(url=stack_env["POSTGRES_URL"])


@pytest.fixture
async def engine(stack_env: dict[str, str], seeded: None) -> AsyncIterator[AsyncEngine]:
    """The worker's RLS engine (loop-scoped per test, the Slice 6 pattern)."""
    from dis_rls import create_rls_engine

    eng = create_rls_engine(stack_env["POSTGRES_URL"])
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def dis_admin(stack_env: dict[str, str]) -> Iterator[Engine]:
    """Admin engine on ithina_dis_db (bypasses RLS) for independent re-reads."""
    url = make_url(stack_env["POSTGRES_ADMIN_URL"])
    assert url.database == "ithina_dis_db"  # target safety for the fixture itself
    assert url.port == 5433
    eng = create_engine(stack_env["POSTGRES_ADMIN_URL"])
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def cleanup_traces(dis_admin: Engine) -> Iterator[list[UUID]]:
    """Collects trace_ids; teardown deletes their bronze + audit rows (admin)."""
    traces: list[UUID] = []
    yield traces
    if traces:
        with dis_admin.begin() as conn:
            conn.execute(
                text("DELETE FROM bronze.data_ingress_events WHERE trace_id = ANY(:tids)"),
                {"tids": traces},
            )
            conn.execute(
                text("DELETE FROM audit.events WHERE trace_id = ANY(:tids)"),
                {"tids": traces},
            )


@pytest.fixture
def storage(stack_env: dict[str, str]) -> StorageClient:
    """The dis-storage client on the bronze bucket (created idempotently)."""
    from google.api_core.exceptions import Conflict

    from dis_storage.client import StorageClient

    bucket = stack_env["GCS_BUCKET_BRONZE"]
    try:
        client = StorageClient(bucket=bucket)
        try:
            client._client.create_bucket(bucket)
        except Conflict:
            pass
    except StackRequiredError:
        raise
    except Exception as exc:
        raise StackRequiredError(
            f"GCS emulator unreachable ({exc!r}); refusing to skip. make run-local."
        ) from exc
    return client
