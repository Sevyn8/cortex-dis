"""The DraftStore interface + an in-memory implementation (PR1; PR2 = real table).

A draft IR is a ``SchemaIR`` with a ``status`` (draft|published|superseded) and a
``schema_version``. Status and version are STORE-managed, never client-settable:
``update`` only persists edits within a draft (status stays ``draft``), and
``freeze`` is the SOLE transition to ``published`` — and freeze runs the ratify gate
intrinsically, so no path to a published version can skip it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

# BOUNDARY (A4): dis-ui-server depends on dis-codegen for the PURE IR ops only --
# SchemaIR and the ratify gate. It must NOT import the generator (render_*); code
# generation stays out-of-band per the freeze-not-generate publish model. Reaching
# for render_* here is a visible violation of that boundary.
from dis_codegen import SchemaIR, assert_ratified_for_publish
from dis_core.errors import DraftStateConflictError, ResourceNotFoundError
from dis_core.ids import new_uuid7


@dataclass(frozen=True)
class DraftSummary:
    """One row of the verticals/drafts registry (the A4 registry surface)."""

    draft_id: str
    vertical: str
    status: str
    schema_version: int


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
        return [
            DraftSummary(draft_id, s.vertical, s.status, s.schema_version)
            for draft_id, s in self._drafts.items()
        ]
