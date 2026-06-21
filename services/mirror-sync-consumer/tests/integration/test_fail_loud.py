"""Criterion 4 (integration side): fail loud before writing; the silent-zero trap is real.

The non-skippable anchor for criterion 4 is the pure-guard unit test
(``tests/unit/test_reader_guards.py``); these add the runtime evidence: a wrong CM target
raises before any read of rows, and a read without the platform context really does return
zero rows under the harness's FORCE RLS (the trap the context assertion protects against).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from dis_core.errors import CustomerMasterReadError
from dis_core.ids import new_uuid7
from mirror_sync_consumer.config import MirrorSyncConfig
from mirror_sync_consumer.pull.reader import create_cm_engine, read_customer_master

pytestmark = pytest.mark.integration


async def test_wrong_cm_target_raises_before_reading(cm_reader_url: str, user_url: str) -> None:
    # Expected CM db name deliberately wrong: the target guard fires before any row read.
    config = MirrorSyncConfig(cm_db_url=cm_reader_url, dis_db_url=user_url, cm_db_name="not_the_cm_db")
    engine = create_cm_engine(cm_reader_url)
    try:
        with pytest.raises(CustomerMasterReadError):
            await read_customer_master(engine, config, trace_id=new_uuid7())
    finally:
        await engine.dispose()


def test_raw_read_without_platform_context_returns_zero(cm_reader_url: str) -> None:
    # The harness's FORCE RLS is faithful: no GUC -> zero rows (silently); PLATFORM -> all rows.
    engine = create_engine(cm_reader_url)  # ithina_dis_user, NOBYPASSRLS
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM core.tenants")).scalar_one() == 0
        with engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_type', 'PLATFORM', true)"))
            assert conn.execute(text("SELECT count(*) FROM core.tenants")).scalar_one() > 0
    finally:
        engine.dispose()
