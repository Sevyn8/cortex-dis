"""``POST /mapping-suggestions``: per-column canonical-field suggestions.

Thin like the other handlers: ``require_tenant`` (the verified token is the sole
scope source), read the startup-built field catalog and the suggester from
``app.state``, delegate to ``GeminiSuggester.suggest`` (Gemini when a key is set,
mechanical fallback otherwise), return the wire model. No DB, no writes; the
suggester never raises on LLM trouble (it degrades to the fallback), so this
handler has no LLM error path of its own. See the contract:
docs/slices/llm-mapping-suggestion-contract.md.

NOTE (phase 1): ``app.state.gemini`` is wired in the lifespan by a later phase;
this handler only reads it. Tests inject a fake suggester on ``app.state.gemini``
and mount this router through the ``create_app(extra_api_routers=...)`` test seam.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from dis_ui_server.auth.identity import Identity
from dis_ui_server.auth.scope import require_tenant, tenant_uuid_of
from dis_ui_server.mapping_validation import require_template_type
from dis_ui_server.schemas.mapping_suggestions import (
    MappingSuggestionRequest,
    MappingSuggestionResponse,
)

router = APIRouter()


@router.post("/mapping-suggestions")
async def post_mapping_suggestions(
    request: Request,
    body: MappingSuggestionRequest,
    identity: Annotated[Identity, Depends(require_tenant)],
) -> MappingSuggestionResponse:
    """Suggest a canonical field per source column; ``source`` flags llm vs fallback.

    Type-aware (D90): with a valid ``template_type`` the suggester scores against THAT type's
    per-type catalog (``app.state.field_catalogs[template_type]``, snapshot included); WITHOUT
    one it falls back to the legacy ``sales + inventory_change`` union (``app.state.field_catalog``)
    so the not-yet-retired /upload flow is unchanged. An invalid type is a clean 400.
    """
    if body.template_type is None:
        catalog = request.app.state.field_catalog
    else:
        require_template_type(body.template_type, tenant_id=str(tenant_uuid_of(identity)))
        catalog = request.app.state.field_catalogs[body.template_type]
    suggester = request.app.state.gemini
    source, model, suggestions = await suggester.suggest(body.columns, catalog)
    return MappingSuggestionResponse(source=source, model=model, suggestions=suggestions)
