"""Fixtures for the Slice 7 DB-pull integration tests.

These tests WRITE Postgres, so — like the Slice 4 RLS isolation test — they must not skip
silently when the stack is absent: a missing env is a loud ERROR, not a skip (the slice's
"errors, never skips" rule for the load-bearing proofs). They read the **test** Customer
Master (the in-cluster ``ithina_platform_db`` on 5433, provisioned by
``dis_testing.customer_master_db``) and write the DIS database (``ithina_dis_db`` on 5433);
the real CM (5432) is never touched (criterion 8).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url


class StackRequiredError(RuntimeError):
    """The DIS stack is required for these load-bearing tests but is absent."""


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = os.environ.get("POSTGRES_ADMIN_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_ADMIN_URL is not set — the Slice 7 DB-pull integration tests need the "
            "admin role to provision the test Customer Master database. Bring up the stack "
            "(make run-local) and export POSTGRES_ADMIN_URL (5433 / ithina_dis_db)."
        )
    return url


@pytest.fixture(scope="session")
def user_url() -> str:
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise StackRequiredError(
            "POSTGRES_URL is not set — the Slice 7 DB-pull integration tests refuse to skip "
            "silently. Bring up the stack and export POSTGRES_URL (5433 / ithina_dis_db)."
        )
    return url


@pytest.fixture(scope="session")
def cm_reader_url(admin_url: str, user_url: str) -> str:
    """Provision the test CM (idempotent) and return the read DSN, asserting it is in-cluster."""
    from dis_testing.customer_master_db import (
        CM_TEST_DB_NAME,
        provision_test_cm,
        reader_url_from,
    )

    provision_test_cm(admin_url)
    url = reader_url_from(user_url)
    parsed = make_url(url)
    # Criterion 8: the test CM is the in-cluster stand-in (5433), never the real CM (5432).
    assert parsed.database == CM_TEST_DB_NAME
    assert parsed.port == 5433
    return url


@pytest.fixture
def cm_admin(admin_url: str) -> Iterator[Engine]:
    """Admin engine on the test CM database (superuser → bypasses RLS) for re-reads/mutations."""
    from dis_testing.customer_master_db import cm_admin_engine

    engine = cm_admin_engine(admin_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def dis_admin(admin_url: str) -> Iterator[Engine]:
    """Admin engine on ithina_dis_db for independent re-reads of identity_mirror."""
    engine = create_engine(admin_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def run_env(monkeypatch: pytest.MonkeyPatch, cm_reader_url: str, user_url: str) -> None:
    """Point a sync run at the test CM (read) and the DIS database (write)."""
    monkeypatch.setenv("CM_DB_URL", cm_reader_url)
    monkeypatch.setenv("POSTGRES_URL", user_url)
    monkeypatch.delenv("CM_DB_NAME", raising=False)  # default ithina_platform_db
