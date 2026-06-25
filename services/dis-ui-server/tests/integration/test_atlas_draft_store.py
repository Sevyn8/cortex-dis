"""PostgresDraftStore against the live stack (A4 PR2) — stack-required.

Exercises the durable store over ``atlas.schema_drafts`` (after migration 0013):
create/get round-trip, update, freeze + per-(vertical, table_key) versioning, and
immutability BOTH ways — the partial-unique conflict on a duplicate published version
AND the DB trigger rejecting an UPDATE on a published row (asserted at the DB level,
not just the app contract). Also verifies a PLATFORM session can write the non-RLS
table, and that freeze (draft->published) passes the trigger.

``stack_env`` (the shared fixture) sources POSTGRES_URL from the running stack and
raises StackRequiredError if absent, so a bare ``uv run pytest`` does not silently
skip. The no-DB foundation CI does not run @pytest.mark.integration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import text

from dis_codegen.ir import FieldIR, ProducedBy, SchemaIR, TableIR
from dis_core.errors import DraftStateConflictError, ResourceNotFoundError
from dis_rls import create_rls_engine, rls_platform_session
from dis_ui_server.atlas import PostgresDraftStore

pytestmark = pytest.mark.integration


@pytest.fixture
async def store(stack_env: dict[str, str]) -> AsyncIterator[PostgresDraftStore]:
    engine = create_rls_engine(stack_env["POSTGRES_URL"])
    try:
        yield PostgresDraftStore(engine)
    finally:
        await engine.dispose()


def _ratified_draft(*, vertical: str, table_key: str) -> SchemaIR:
    """A minimal RATIFIED draft: the sole field is the natural-key member, mandatory,
    origin human, so ratify_violations is empty and freeze succeeds."""
    field = FieldIR(
        name="sku_id",
        produced_by=ProducedBy.MAPPING_PRODUCED,
        type_ref="str",
        nullable=False,
        max_length=128,
        mandatory=True,
        origin="human",
        display_name="SKU",
        description="The product identifier.",
        section="identity",
    )
    table = TableIR(
        key=table_key,
        template_type="snapshot",
        semantics="merge_upsert",
        sink=f"canonical.{table_key}",
        natural_key=("sku_id",),
        fields=(field,),
    )
    return SchemaIR(
        vertical=vertical,
        schema_version=1,
        status="draft",
        system_profile="dis.v1",
        types={},
        enums={},
        tables=(table,),
    )


def _key() -> str:
    return f"t_{uuid4().hex[:12]}"  # unique table_key per test (no cross-test collisions)


async def test_create_get_round_trips_the_ir_through_jsonb(store: PostgresDraftStore) -> None:
    draft = _ratified_draft(vertical="retail", table_key=_key())
    draft_id = await store.create(draft)
    loaded = await store.get(draft_id)
    assert loaded == draft  # full IR fidelity through schema_to_document -> JSONB -> parse_ir


async def test_get_missing_raises_not_found(store: PostgresDraftStore) -> None:
    with pytest.raises(ResourceNotFoundError):
        await store.get(str(uuid4()))


async def test_update_persists_edits_and_refuses_a_published_row(store: PostgresDraftStore) -> None:
    table_key = _key()
    draft_id = await store.create(_ratified_draft(vertical="retail", table_key=table_key))
    # Edit a draft: allowed.
    edited = _ratified_draft(vertical="retail", table_key=table_key)
    await store.update(draft_id, edited)
    # Freeze, then update -> app contract refuses a non-draft (409-mapped).
    await store.freeze(draft_id, version=1)
    with pytest.raises(DraftStateConflictError):
        await store.update(draft_id, edited)


async def test_freeze_passes_the_trigger_and_publishes(store: PostgresDraftStore) -> None:
    # (a) draft->published: the row's OLD status is 'draft', so the immutability trigger
    # does NOT fire; freeze succeeds and the version is assigned.
    draft_id = await store.create(_ratified_draft(vertical="retail", table_key=_key()))
    frozen = await store.freeze(draft_id, version=1)
    assert frozen.status == "published"
    assert frozen.schema_version == 1
    assert (await store.get(draft_id)).status == "published"


async def test_published_row_update_is_rejected_by_the_trigger(store: PostgresDraftStore) -> None:
    # (b) a raw UPDATE on the now-published row is rejected AT THE DB LEVEL by the
    # trigger, independent of the app contract.
    draft_id = await store.create(_ratified_draft(vertical="retail", table_key=_key()))
    await store.freeze(draft_id, version=1)
    with pytest.raises(Exception, match="published and immutable"):
        async with rls_platform_session(store._engine) as conn:
            await conn.execute(
                text("UPDATE atlas.schema_drafts SET vertical = 'tampered' WHERE id = :id"),
                {"id": draft_id},
            )


async def test_duplicate_published_version_conflicts_on_the_partial_unique_index(
    store: PostgresDraftStore,
) -> None:
    table_key = _key()  # same (vertical, table_key) for both drafts
    first = await store.create(_ratified_draft(vertical="retail", table_key=table_key))
    second = await store.create(_ratified_draft(vertical="retail", table_key=table_key))
    await store.freeze(first, version=1)
    # Freezing the second to the SAME published version violates the partial unique index.
    with pytest.raises(Exception, match="uq_schema_drafts_published_version|unique"):
        await store.freeze(second, version=1)


async def test_platform_session_writes_the_non_rls_table(store: PostgresDraftStore) -> None:
    # The no-policy verification: a PLATFORM (no-tenant) session writes the non-RLS
    # atlas table (the "writes nothing" rule is the tenant-scoped WITH CHECK, absent here).
    draft_id = await store.create(_ratified_draft(vertical="retail", table_key=_key()))
    summaries = await store.list_drafts()
    assert any(s.draft_id == draft_id for s in summaries)
