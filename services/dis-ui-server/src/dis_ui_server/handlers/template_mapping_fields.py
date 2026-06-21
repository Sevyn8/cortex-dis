"""``GET /template-types`` + the type-aware ``GET /template-mapping-fields`` (slice 14b b / 14d).

Both are identical for every tenant; NOT tenant-scoped, open no ``rls_session``,
touch no database — the per-type catalogs were built once at startup
(``app.state.field_catalogs``) from the same drift-guarded sources the create/edit
validator uses (one canonical truth). Authenticated (UI data surface), but any
verified identity qualifies: tenant or ops, no tenant context required —
test-proven byte-identical across callers.

``template-mapping-fields`` is type-aware (Slice 14d): the field set is selected
by the required ``?template_type=`` query parameter. A missing or unknown type is
a 400 ``InvalidTemplateTypeError`` through the §2.3 envelope (never a half-served
or empty catalog). The vocabulary is the one in-code definition
(``dis_validation.TEMPLATE_TYPES``); the display copy below is presentation only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from dis_core.errors import InvalidTemplateTypeError
from dis_ui_server.auth.identity import Identity
from dis_ui_server.auth.scope import get_current_identity
from dis_ui_server.schemas.mapping_fields import TemplateMappingField
from dis_ui_server.schemas.template_types import TemplateType
from dis_validation import INVENTORY_CHANGE, SALES, SNAPSHOT, TEMPLATE_TYPES, is_template_type

router = APIRouter()

# Operator-facing presentation copy, keyed off the in-code vocabulary. Presentation
# lives in the BFF; the KEYS are the single source (dis_validation.TEMPLATE_TYPES).
_TYPE_DISPLAY: dict[str, tuple[str, str]] = {
    SNAPSHOT: (
        "Catalogue / current position",
        "Map a file that lists each product's current details and stock position in your store.",
    ),
    SALES: ("Sales events", "Map a sales-transaction file into sale events."),
    INVENTORY_CHANGE: (
        "Change events",
        "Map an inventory / price / status change file into change events.",
    ),
}


@router.get("/template-types")
async def get_template_types(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> list[TemplateType]:
    """The allowed template types (key, display_name, description), served from the vocabulary."""
    return [
        TemplateType(key=key, display_name=_TYPE_DISPLAY[key][0], description=_TYPE_DISPLAY[key][1])
        for key in TEMPLATE_TYPES
    ]


@router.get("/template-mapping-fields")
async def get_template_mapping_fields(
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    template_type: str | None = None,
) -> list[TemplateMappingField]:
    """The field set for ``template_type``, stable order (section, then declaration); bare array."""
    if template_type is None:
        raise InvalidTemplateTypeError(
            "template_type query parameter is required; expected one of "
            f"{list(TEMPLATE_TYPES)} (GET /template-types)",
        )
    if not is_template_type(template_type):
        raise InvalidTemplateTypeError(
            f"unknown template_type {template_type!r}; expected one of {list(TEMPLATE_TYPES)}",
            template_type=template_type,
        )
    catalogs: dict[str, list[TemplateMappingField]] = request.app.state.field_catalogs
    return catalogs[template_type]
