"""The DraftStore interface + an in-memory implementation (PR1; PR2 = real table).

A draft IR is a ``SchemaIR`` with a ``status`` (draft|published|superseded) and a
``schema_version``. Status and version are STORE-managed, never client-settable:
``update`` only persists edits within a draft (status stays ``draft``), and
``freeze`` is the SOLE transition to ``published`` — and freeze runs the ratify gate
intrinsically, so no path to a published version can skip it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# BOUNDARY (A4): dis-ui-server depends on dis-codegen for the PURE IR ops only --
# SchemaIR, the ratify gate, and the single IR serializer (schema_to_document). It
# must NOT import the generator (render_*); code generation stays out-of-band per the
# freeze-not-generate publish model. Reaching for render_* here is a visible violation.
from dis_codegen import SchemaIR, assert_ratified_for_publish, schema_to_document
from dis_codegen.ir import parse_ir
from dis_core.errors import DraftStateConflictError, ResourceNotFoundError
from dis_core.ids import new_uuid7
from dis_rls import rls_platform_session


@dataclass(frozen=True)
class DraftSummary:
    """One row of the verticals/drafts registry (the A4 registry surface) — LEAN: no IR.

    PR3a additively enriches this with ``table_key`` and the timestamps the registry
    surface shows. The timestamps are Optional because only the durable store has them:
    the in-memory double tracks none and returns ``None``. The five ``DraftStore`` method
    signatures, the ratify gate, and the draft/publish endpoints are unchanged."""

    draft_id: str
    vertical: str
    table_key: str
    status: str
    schema_version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    published_at: datetime | None = None


@runtime_checkable
class DraftStore(Protocol):
    """Persistence for Atlas draft IRs. The gate lives in ``freeze``, not the store's
    callers, so freeze cannot be bypassed by writing a published status directly."""

    async def create(self, draft: SchemaIR) -> str: ...
    async def get(self, draft_id: str) -> SchemaIR: ...
    async def update(self, draft_id: str, draft: SchemaIR) -> None: ...
    async def freeze(self, draft_id: str, version: int) -> SchemaIR: ...
    async def list_drafts(self) -> list[DraftSummary]: ...


class InMemoryDraftStore:
    """Test/dev DraftStore. The real platform-scoped table + migration is PR2."""

    def __init__(self) -> None:
        self._drafts: dict[str, SchemaIR] = {}

    async def create(self, draft: SchemaIR) -> str:
        draft_id = str(new_uuid7())
        self._drafts[draft_id] = draft
        return draft_id

    async def get(self, draft_id: str) -> SchemaIR:
        try:
            return self._drafts[draft_id]
        except KeyError:
            raise ResourceNotFoundError(
                "atlas draft not found", resource="atlas_draft", identifier=draft_id
            ) from None

    async def update(self, draft_id: str, draft: SchemaIR) -> None:
        """Persist edits to a draft. Refuses if the stored draft is no longer a draft
        (published versions are immutable) or if the edit tries to change status:
        ``freeze`` is the only publish transition, so ``update`` can never reach
        ``published``."""
        current = await self.get(draft_id)
        if current.status != "draft":
            raise DraftStateConflictError(
                "cannot edit a non-draft version (published versions are immutable)",
                draft_id=draft_id,
                expected="draft",
                actual=current.status,
            )
        if draft.status != "draft":
            raise DraftStateConflictError(
                "update cannot change draft status; freeze is the only publish transition",
                draft_id=draft_id,
                expected="draft",
                actual=draft.status,
            )
        self._drafts[draft_id] = draft

    async def freeze(self, draft_id: str, version: int) -> SchemaIR:
        """The SOLE transition to ``published``. Runs the ratify gate intrinsically
        (``assert_ratified_for_publish``) before freezing, so no caller can publish an
        unratified draft. Returns the frozen, immutable, versioned IR."""
        current = await self.get(draft_id)
        if current.status != "draft":
            raise DraftStateConflictError(
                "only a draft can be frozen",
                draft_id=draft_id,
                expected="draft",
                actual=current.status,
            )
        assert_ratified_for_publish(current)  # intrinsic gate: freeze cannot bypass it
        frozen = replace(current, status="published", schema_version=version)
        self._drafts[draft_id] = frozen
        return frozen

    async def list_drafts(self) -> list[DraftSummary]:
        # table_key from the SchemaIR; the in-memory double tracks no timestamps (None).
        return [
            DraftSummary(
                draft_id=draft_id,
                vertical=s.vertical,
                table_key=s.tables[0].key,
                status=s.status,
                schema_version=s.schema_version,
            )
            for draft_id, s in self._drafts.items()
        ]


class PostgresDraftStore:
    """Durable DraftStore over ``atlas.schema_drafts`` (A4 PR2, decisions.md D104).

    Platform-scoped, non-RLS table written through ``rls_platform_session`` (the GUC
    is inert on a policy-less table). The IR is stored as a JSONB document via the
    single ``schema_to_document`` serializer and loaded back with ``parse_ir`` — the
    same shape ``emit_draft_ir`` uses, so there is no second IR shape. Same contract as
    ``InMemoryDraftStore``: ``update`` refuses a non-draft (409); ``freeze`` is the sole
    ``published`` transition and runs the frozen ratify gate. A DB trigger backstops
    published-row immutability beneath this contract.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, draft: SchemaIR) -> str:
        draft_id = str(new_uuid7())
        table = draft.tables[0]
        async with rls_platform_session(self._engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO atlas.schema_drafts "
                    "(id, vertical, table_key, status, schema_version, ir) "
                    "VALUES (:id, :vertical, :table_key, :status, :schema_version, CAST(:ir AS JSONB))"
                ),
                {
                    "id": draft_id,
                    "vertical": draft.vertical,
                    "table_key": table.key,
                    "status": draft.status,
                    "schema_version": draft.schema_version,
                    "ir": json.dumps(schema_to_document(draft)),
                },
            )
        return draft_id

    async def get(self, draft_id: str) -> SchemaIR:
        async with rls_platform_session(self._engine) as conn:
            row = (
                (
                    await conn.execute(
                        text("SELECT ir FROM atlas.schema_drafts WHERE id = :id"), {"id": draft_id}
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ResourceNotFoundError("atlas draft not found", resource="atlas_draft", identifier=draft_id)
        return parse_ir(dict(row["ir"]))  # jsonb -> dict -> SchemaIR (the shared shape)

    async def update(self, draft_id: str, draft: SchemaIR) -> None:
        """Persist edits to a draft; refuses a non-draft target (published is immutable)
        and cannot change status to published (freeze is the only publish transition)."""
        current = await self.get(draft_id)
        if current.status != "draft":
            raise DraftStateConflictError(
                "cannot edit a non-draft version (published versions are immutable)",
                draft_id=draft_id,
                expected="draft",
                actual=current.status,
            )
        if draft.status != "draft":
            raise DraftStateConflictError(
                "update cannot change draft status; freeze is the only publish transition",
                draft_id=draft_id,
                expected="draft",
                actual=draft.status,
            )
        async with rls_platform_session(self._engine) as conn:
            await conn.execute(
                text(
                    "UPDATE atlas.schema_drafts SET ir = CAST(:ir AS JSONB), updated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": draft_id, "ir": json.dumps(schema_to_document(draft))},
            )

    async def freeze(self, draft_id: str, version: int) -> SchemaIR:
        """The SOLE transition to ``published``. Runs the frozen ratify gate before the
        UPDATE; the row's OLD status is 'draft', so the immutability trigger does not
        fire on this transition (it only rejects mutating an already-published row)."""
        current = await self.get(draft_id)
        if current.status != "draft":
            raise DraftStateConflictError(
                "only a draft can be frozen",
                draft_id=draft_id,
                expected="draft",
                actual=current.status,
            )
        assert_ratified_for_publish(current)  # intrinsic gate: freeze cannot bypass it
        frozen = replace(current, status="published", schema_version=version)
        async with rls_platform_session(self._engine) as conn:
            await conn.execute(
                text(
                    "UPDATE atlas.schema_drafts "
                    "SET status = 'published', schema_version = :version, "
                    "ir = CAST(:ir AS JSONB), published_at = now(), updated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": draft_id, "version": version, "ir": json.dumps(schema_to_document(frozen))},
            )
        return frozen

    async def list_drafts(self) -> list[DraftSummary]:
        async with rls_platform_session(self._engine) as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, vertical, table_key, status, schema_version, "
                            "created_at, updated_at, published_at FROM atlas.schema_drafts"
                        )
                    )
                )
                .mappings()
                .all()
            )
        return [
            DraftSummary(
                draft_id=str(r["id"]),
                vertical=r["vertical"],
                table_key=r["table_key"],
                status=r["status"],
                schema_version=r["schema_version"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                published_at=r["published_at"],
            )
            for r in rows
        ]
