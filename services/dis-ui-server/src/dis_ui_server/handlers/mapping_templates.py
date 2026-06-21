"""The ``/mapping-templates`` resource (slice 14b c/d/e) — template-grain CRUD.

The resource is the TEMPLATE (a lineage of versions, D68); ``{template_id}`` is
the only URL key. Reads and writes run through ``repos/mapping_templates.py``
(``rls_session``, tenant from token). Detail/PATCH lookups are throw-style 404
(``ResourceNotFoundError``) — under RLS, absent and other-tenant are the same
404, no existence oracle. Create writes a single ACTIVE row (Slice 16c, D88:
translate -> validate -> ACTIVE write, no DRAFT/staging); PATCH edits a DRAFT in
place or chains a new DRAFT. The promote/deprecate transitions and the
``mapping.changed`` publish are a later slice.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_core.errors import ResourceNotFoundError
from dis_core.ids import new_uuid7
from dis_mapping import SourceMapping
from dis_ui_server.auth.identity import Identity
from dis_ui_server.auth.scope import (
    ReadScope,
    WriteScope,
    get_current_identity,
    require_read_scope,
    require_write_scope,
    resolve_acted_for,
)
from dis_ui_server.mapping_translation import translate_columns_to_mapping_rules
from dis_ui_server.mapping_validation import require_template_type, validate_mapping_rules_for_type
from dis_ui_server.repos.mapping_templates import (
    create_template,
    get_template_rows,
    list_template_rows,
    patch_template,
)
from dis_ui_server.schemas.mapping_templates import (
    MappingTemplate,
    MappingTemplateCreate,
    MappingTemplateDetail,
    MappingTemplatePatch,
    MappingTemplateStatus,
    MappingTemplateVersion,
)

router = APIRouter()

# §2.6 vocabulary translation, pinned explicitly (a new DB status fails loud).
_STATUS_WIRE: dict[str, MappingTemplateStatus] = {
    "DRAFT": "draft",
    "STAGED": "staged",
    "ACTIVE": "active",
    "DEPRECATED": "deprecated",
}


def _created_by_uuid(identity: Identity) -> UUID | None:
    """The token ``sub`` as a UUID where it is one; NULL otherwise (column is
    nullable by design — the real claim vocabulary is unsigned, D56/Blocker 5)."""
    try:
        return UUID(identity.user_id)
    except ValueError:
        return None


def _to_version(row: Row[Any]) -> MappingTemplateVersion:
    # Stored rules always parse: every write validated them (a failure here is
    # real corruption and a correct 500, never papered over).
    rules = SourceMapping.model_validate(row.mapping_rules)
    return MappingTemplateVersion(
        mapping_version_id=row.mapping_version_id,
        version=row.version_seq_per_source,
        status=_STATUS_WIRE[row.status],
        mapping_rules=rules,
        field_count=len(rules.rename),
        transform_count=(
            sum(len(specs) for specs in rules.normalize.values())
            + len(rules.cast)
            + sum(len(specs) for specs in rules.derive.values())
        ),
        predecessor_version_id=row.predecessor_version_id,
        created_at=row.created_at,
        created_by_user_id=str(row.created_by_user_id) if row.created_by_user_id else None,
        activated_at=row.activated_at,
        deprecated_at=row.deprecated_at,
    )


def _version_seq_for(rows: Sequence[Row[Any]], status: str) -> int | None:
    return next((row.version_seq_per_source for row in rows if row.status == status), None)


def _summary_fields(rows: Sequence[Row[Any]]) -> dict[str, Any]:
    """Lineage summary off one template's rows (any order)."""
    newest = max(rows, key=lambda row: row.version_seq_per_source)
    return {
        "template_id": str(newest.template_id),
        "source_id": newest.source_id,
        "template_name": newest.template_name,
        "template_type": newest.template_type,  # lineage-fixed; any row carries it
        "latest_version": newest.version_seq_per_source,
        "active_version": _version_seq_for(rows, "ACTIVE"),
        "staged_version": _version_seq_for(rows, "STAGED"),
        "draft_version": _version_seq_for(rows, "DRAFT"),
        "versions_count": len(rows),
        "created_at": min(row.created_at for row in rows),
        "latest_version_created_at": max(row.created_at for row in rows),
    }


def _to_detail(rows: Sequence[Row[Any]]) -> MappingTemplateDetail:
    ordered = sorted(rows, key=lambda row: row.version_seq_per_source, reverse=True)
    return MappingTemplateDetail(
        **_summary_fields(ordered),
        versions=[_to_version(row) for row in ordered],
    )


@router.get("/mapping-templates")
async def list_mapping_templates(
    request: Request,
    scope: Annotated[ReadScope, Depends(require_read_scope)],
    source_id: str | None = None,
) -> list[MappingTemplate]:
    """Template lineage summaries, order (source_id, template_name). TENANT sees its own;
    PLATFORM (user_type=PLATFORM + dis:ops) sees every tenant's."""
    engine: AsyncEngine = request.app.state.engine
    rows = await list_template_rows(engine, scope, source_id=source_id)
    by_template: dict[UUID, list[Row[Any]]] = {}
    for row in rows:  # rows arrive lineage-grouped; group defensively anyway
        by_template.setdefault(row.template_id, []).append(row)
    summaries = [MappingTemplate(**_summary_fields(group)) for group in by_template.values()]
    return sorted(summaries, key=lambda t: (t.source_id, t.template_name))


@router.get("/mapping-templates/{template_id}")
async def get_mapping_template(
    request: Request,
    scope: Annotated[ReadScope, Depends(require_read_scope)],
    template_id: UUID,
) -> MappingTemplateDetail:
    """One template with its full version lineage; 404 throw-style when invisible."""
    engine: AsyncEngine = request.app.state.engine
    rows = await get_template_rows(engine, scope, template_id)
    if not rows:
        raise ResourceNotFoundError(
            f"mapping template {template_id} not found",
            resource="mapping_template",
            identifier=str(template_id),
            tenant_id=str(scope.tenant_id) if scope.tenant_id is not None else None,
        )
    return _to_detail(rows)


@router.post("/mapping-templates", status_code=201)
async def create_mapping_template(
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    write_scope: Annotated[WriteScope, Depends(require_write_scope)],
    body: MappingTemplateCreate,
) -> MappingTemplateDetail:
    """Create a template (Slice 16c: translate -> validate -> ACTIVE write).

    The request carries semantic intent per column (``src_key`` -> ``dest_key`` +
    source-format declarations), not engine ops. This endpoint, in order:

    1. checks ``template_type`` against the in-code vocabulary (a clean 400);
    2. TRANSLATES the columns + declarations into a ``mapping_rules`` document
       (``mapping_translation``) — an unknown date token is a 400 here;
    3. runs the full semantic gate (``validate_mapping_rules_for_type``: target
       legality, mandatory coverage, presence pairings) — any failure is a 400
       BEFORE any DB touch, so an invalid request never opens an ``rls_session``;
    4. mints a fresh ``template_id`` (UUIDv7) and writes a single ACTIVE row via
       ``create_template`` (status ACTIVE, ``activated_at`` stamped, version minted).

    Single state: success writes ACTIVE (no DRAFT, no staging); any failure is a 4xx
    with nothing persisted. Returns the real ``MappingTemplateDetail`` off the row.
    """
    engine: AsyncEngine = request.app.state.engine
    # Acted-for tenant discriminated by the VERIFIED user_type (Slice 17b): TENANT pins to
    # its token tenant (a body acting_for is REJECTED 403); PLATFORM+dis:ops writes the
    # body's acted-for tenant; PLATFORM without dis:ops or without an acted-for tenant -> 403.
    acted_for = resolve_acted_for(write_scope, body.acting_for_tenant_id)
    require_template_type(body.template_type, tenant_id=str(acted_for))  # 400 if outside vocab
    raw_rules = translate_columns_to_mapping_rules(body, tenant_id=str(acted_for))  # 400 on bad token
    # The gate is the LAST step before the write: a semantic failure (bad dest_key,
    # missing mandatory, broken presence pairing) raises here, never persisting. We store
    # the gate's VALIDATED document (its canonical dump — non-decimal casts gain explicit
    # null precision/scale), so the write can never carry un-gated rules.
    source = validate_mapping_rules_for_type(
        raw_rules, template_type=body.template_type, tenant_id=str(acted_for)
    )
    row = await create_template(
        engine,
        acted_for,
        template_id=new_uuid7(),  # fresh per create => no prior ACTIVE for the triple
        source_id=body.source_id,
        template_name=body.template_name,
        template_type=body.template_type,
        mapping_rules=source.model_dump(mode="json"),
        created_by_user_id=_created_by_uuid(identity),
        user_type=write_scope.user_type,
    )
    return _to_detail([row])


@router.patch("/mapping-templates/{template_id}")
async def patch_mapping_template(
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    write_scope: Annotated[WriteScope, Depends(require_write_scope)],
    template_id: UUID,
    body: MappingTemplatePatch,
) -> MappingTemplateDetail:
    """Edit a template per the D17 lifecycle (see the repo's recorded semantics)."""
    engine: AsyncEngine = request.app.state.engine
    acted_for = resolve_acted_for(write_scope, body.acting_for_tenant_id)
    rules_dump: dict[str, Any] | None = None
    if body.mapping_rules is not None:
        # template_type is lineage-fixed (set at creation): re-validate the edited
        # rules against the STORED type. The type is immutable, so reading it ahead
        # of patch_template's locked re-read is safe (nothing can change it). Pinned to
        # the acted-for tenant: a PLATFORM impersonation acts AS that tenant; see-all is
        # not needed to read the single template being patched.
        rows = await get_template_rows(engine, ReadScope(is_platform=False, tenant_id=acted_for), template_id)
        if not rows:
            raise ResourceNotFoundError(
                f"mapping template {template_id} not found",
                resource="mapping_template",
                identifier=str(template_id),
                tenant_id=str(acted_for),
            )
        source = validate_mapping_rules_for_type(
            body.mapping_rules, template_type=rows[0].template_type, tenant_id=str(acted_for)
        )
        rules_dump = source.model_dump(mode="json")
    rows = await patch_template(
        engine,
        acted_for,
        template_id,
        template_name=body.template_name,
        mapping_rules=rules_dump,
        created_by_user_id=_created_by_uuid(identity),
        user_type=write_scope.user_type,
    )
    return _to_detail(rows)
