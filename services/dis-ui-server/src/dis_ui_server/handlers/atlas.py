"""``/atlas/*``: the Atlas console BFF (A4 PR1) — draft / ratify / publish.

Super-Admin-only. Upload example CSVs and the A3 path (profile + propose +
assemble) produces a draft IR; PATCH edits and ratifies (flips curated attributes
to ``origin: human``); publish runs the ratify gate and FREEZES an immutable
versioned IR plus a publish audit event. Publish does not generate code or run a
migration (freeze-not-generate); A1 generation is out-of-band over the frozen IR.

Thin handlers: domain errors from dis-core (never ``HTTPException``); the A3 modules
and the dis-codegen IR ops are reused as-is.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

# BOUNDARY (A4): the BFF uses dis-codegen for PURE IR ops only (assemble + the
# ratify gate + the fresh-draft validator). It MUST NOT import the generator
# (render_*); code generation is out-of-band (freeze-not-generate).
from dis_codegen import (
    SchemaIR,
    assemble_draft_ir,
    ratify_violations,
    validate_fresh_draft,
)
from dis_codegen.ir import ProducedBy
from dis_core.errors import (
    DraftNotRatifiedError,
    DraftStateConflictError,
    PayloadTooLargeError,
    ResourceNotFoundError,
    UploadRequestError,
)
from dis_core.logging import get_logger
from dis_ui_server.auth.identity import Identity
from dis_ui_server.auth.scope import require_super_admin
from dis_ui_server.config import ATLAS_MAX_UPLOAD_FILES, CSV_UPLOAD_MAX_FILE_BYTES, SERVICE_NAME
from dis_ui_server.infer.profiler import columns_to_payload, profile_csvs
from dis_ui_server.infer.proposer import proposals_to_payload
from dis_ui_server.schemas.atlas import (
    AtlasDraftResponse,
    DraftPatch,
    DraftSummaryModel,
    PublishReceipt,
    draft_to_wire,
)

_log = get_logger(SERVICE_NAME)
_READ_CHUNK = 64 * 1024

router = APIRouter()


async def _read_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an upload under a per-file byte ceiling; loud past it (no full-buffer-then-check)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                "an uploaded example file crossed the per-file size ceiling",
                limit_bytes=max_bytes,
                observed_bytes=total,
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/atlas/verticals/{vertical}/draft")
async def create_draft(
    vertical: str,
    request: Request,
    _admin: Annotated[Identity, Depends(require_super_admin)],
    files: Annotated[list[UploadFile], File()],
    table_key: Annotated[str | None, Form()] = None,
) -> AtlasDraftResponse:
    """Profile the uploaded CSVs, propose canonical fields (degrade-never-raise),
    assemble a draft IR, and persist it. Tempfiles are cleaned on every path."""
    if not files:
        raise UploadRequestError("at least one example CSV is required", part="files")
    if len(files) > ATLAS_MAX_UPLOAD_FILES:
        raise UploadRequestError(
            f"too many example files: {len(files)} (max {ATLAS_MAX_UPLOAD_FILES})", part="files"
        )

    resolved_table_key = table_key or f"{vertical}_snapshot"
    with tempfile.TemporaryDirectory(prefix="atlas-upload-") as tmpdir:
        # All tempfiles live under tmpdir; the context removes them on EVERY path,
        # including the degrade/error path (the directory is unlinked on exit).
        paths: list[Path] = []
        for index, upload in enumerate(files):
            content = await _read_bounded(upload, CSV_UPLOAD_MAX_FILE_BYTES)
            name = Path(upload.filename or f"upload_{index}.csv").name
            path = Path(tmpdir) / f"{index:03d}_{name}"
            path.write_bytes(content)
            paths.append(path)

        profiled = profile_csvs(paths)
        proposer = request.app.state.atlas_proposer
        proposals = await proposer.propose(profiled)  # FieldProposer: degrade-never-raise
        draft = assemble_draft_ir(
            vertical=vertical,
            table_key=resolved_table_key,
            profile_payload=columns_to_payload(profiled),
            proposal_payload=proposals_to_payload(proposals),
        )
    validate_fresh_draft(draft)  # assembly-time sanity (NOT the publish gate)
    store = request.app.state.atlas_store
    draft_id = await store.create(draft)
    return draft_to_wire(draft_id, draft)


@router.get("/atlas/drafts")
async def list_drafts(
    request: Request,
    _admin: Annotated[Identity, Depends(require_super_admin)],
    status: Annotated[Literal["draft", "published", "superseded"] | None, Query()] = None,
) -> list[DraftSummaryModel]:
    """The verticals/drafts registry: a LEAN list (no IR document; the full IR is
    GET /atlas/drafts/{id}). Optional ``?status=`` filters server-side; absent = all.
    FastAPI validates the status value against the vocabulary (422 on an unknown one)."""
    summaries = await request.app.state.atlas_store.list_drafts()
    if status is not None:
        summaries = [s for s in summaries if s.status == status]
    return [
        DraftSummaryModel(
            draft_id=s.draft_id,
            vertical=s.vertical,
            table_key=s.table_key,
            status=s.status,
            schema_version=s.schema_version,
            created_at=s.created_at,
            updated_at=s.updated_at,
            published_at=s.published_at,
        )
        for s in summaries
    ]


@router.get("/atlas/drafts/{draft_id}")
async def get_draft(
    draft_id: str,
    request: Request,
    _admin: Annotated[Identity, Depends(require_super_admin)],
) -> AtlasDraftResponse:
    draft = await request.app.state.atlas_store.get(draft_id)
    return draft_to_wire(draft_id, draft)


@router.patch("/atlas/drafts/{draft_id}")
async def patch_draft(
    draft_id: str,
    patch: DraftPatch,
    request: Request,
    _admin: Annotated[Identity, Depends(require_super_admin)],
) -> AtlasDraftResponse:
    """Edit mapping-produced fields and ratify (flip ``origin`` to ``human``). System
    fields are read-only (IR spec section 4). Edit-legality is the section-4 lock plus
    ``origin in {inferred, human}`` (Literal-enforced); the fresh-draft validator is NOT
    run here (a ratified draft fails it by design)."""
    store = request.app.state.atlas_store
    draft = await store.get(draft_id)
    updated = _apply_patch(draft, patch, draft_id=draft_id)
    await store.update(draft_id, updated)
    return draft_to_wire(draft_id, updated)


@router.post("/atlas/drafts/{draft_id}/publish")
async def publish_draft(
    draft_id: str,
    request: Request,
    _admin: Annotated[Identity, Depends(require_super_admin)],
) -> PublishReceipt:
    """Run the ratify gate, then FREEZE: the draft is rejected (left unpublished)
    while any curated-bearing field is still ``origin: inferred``. On success the
    store freezes an immutable versioned IR (freeze re-runs the gate intrinsically)
    and a publish audit event is emitted (fire-and-forget)."""
    store = request.app.state.atlas_store
    draft = await store.get(draft_id)
    violations = ratify_violations(draft)  # the SAME check freeze enforces
    if violations:
        raise DraftNotRatifiedError(
            "draft has unratified curated attributes; ratify them before publishing",
            violations=tuple(violations),
        )
    frozen = await store.freeze(draft_id, version=draft.schema_version)
    audit_emitted = _emit_publish_audit(request, draft_id, frozen)
    return PublishReceipt(
        draft_id=draft_id,
        vertical=frozen.vertical,
        status=frozen.status,
        schema_version=frozen.schema_version,
        audit_emitted=audit_emitted,
    )


def _apply_patch(draft: SchemaIR, patch: DraftPatch, *, draft_id: str) -> SchemaIR:
    """Apply edits to mapping-produced fields only (system fields are locked)."""
    table = draft.tables[0]
    by_name = {f.name: i for i, f in enumerate(table.fields)}
    fields = list(table.fields)
    for edit in patch.fields:
        if edit.name not in by_name:
            raise ResourceNotFoundError(
                "field not in draft", resource="atlas_draft_field", identifier=edit.name
            )
        field = fields[by_name[edit.name]]
        if field.produced_by is not ProducedBy.MAPPING_PRODUCED:
            raise DraftStateConflictError(
                f"field {edit.name!r} is a locked system field and cannot be edited",
                draft_id=draft_id,
            )
        # Apply each provided attribute with a typed replace (only provided fields change).
        if edit.nullable is not None:
            field = replace(field, nullable=edit.nullable)
        if edit.mandatory is not None:
            field = replace(field, mandatory=edit.mandatory)
        if edit.pii is not None:
            field = replace(field, pii=edit.pii)
        if edit.enum_candidate is not None:
            field = replace(field, enum_candidate=tuple(edit.enum_candidate))
        if edit.origin is not None:
            field = replace(field, origin=edit.origin)
        fields[by_name[edit.name]] = field
    natural_key = tuple(patch.natural_key) if patch.natural_key is not None else table.natural_key
    new_table = replace(table, fields=tuple(fields), natural_key=natural_key)
    return replace(draft, tables=(new_table,))


def _emit_publish_audit(request: Request, draft_id: str, frozen: SchemaIR) -> bool:
    """Fire-and-forget publish audit (hard rule 11). The CM action-ledger binding is
    A5; PR1 emits via an ``emit_atlas_publish`` sink when one is wired (duck-typed so
    a not-yet-extended audit is silently skipped, not an error every publish)."""
    audit = getattr(request.app.state, "audit", None)
    emit = getattr(audit, "emit_atlas_publish", None)
    if emit is None:
        _log.bind(stage="atlas_publish").debug(
            "publish audit sink not wired; skipping (CM action-ledger binding lands in A5)"
        )
        return False
    try:
        emit(draft_id=draft_id, vertical=frozen.vertical, schema_version=frozen.schema_version)
        return True
    except Exception as exc:  # never block the publish on audit (hard rule 11)
        _log.bind(stage="atlas_publish", error=type(exc).__name__).warning("publish audit emit failed")
        return False
