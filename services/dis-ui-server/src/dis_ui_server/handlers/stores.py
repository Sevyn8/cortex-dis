"""``GET /stores-onboarded`` — the token tenant's mirrored stores (slice 14b a).

Tenant from the verified token ONLY (no path/query parameter exists, so none
can be honoured); the read goes through the single ``repos/stores.py``
chokepoint that owns the in-query tenant predicate (``identity_mirror`` is
RLS-OFF, D41 — the registered weak link). Tenant-facing only; the ops
cross-tenant store read is a later endpoint.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_ui_server.auth.identity import Identity
from dis_ui_server.auth.scope import require_tenant, tenant_uuid_of
from dis_ui_server.repos.stores import list_onboarded_stores
from dis_ui_server.schemas.stores import OnboardedStore, StoreStatus, StoreTaxTreatment

router = APIRouter()

# §2.6: the BFF owns vocabulary translation; DB vocab never leaks. Explicit maps
# (not ``.lower()``) so a NEW database vocabulary member fails loud (KeyError →
# 500) instead of silently leaking an untranslated value to the UI.
_STATUS_WIRE: dict[str, StoreStatus] = {
    "OPENING": "opening",
    "ACTIVE": "active",
    "INACTIVE": "inactive",
    "CLOSED": "closed",
}
_TAX_TREATMENT_WIRE: dict[str, StoreTaxTreatment] = {
    "INCLUSIVE": "inclusive",
    "EXCLUSIVE": "exclusive",
}


def _to_wire(row: Row[Any]) -> OnboardedStore:
    return OnboardedStore(
        store_id=str(row.store_id),
        name=row.name,
        store_code=row.store_code,
        status=_STATUS_WIRE[row.status],
        country=row.country,
        timezone=row.timezone,
        currency=row.currency,
        tax_treatment=_TAX_TREATMENT_WIRE[row.tax_treatment],
    )


@router.get("/stores-onboarded")
async def get_stores_onboarded(
    request: Request,
    identity: Annotated[Identity, Depends(require_tenant)],
) -> list[OnboardedStore]:
    """The tenant's onboarded stores, stable order (name, store_id); bare array."""
    engine: AsyncEngine = request.app.state.engine
    rows = await list_onboarded_stores(engine, tenant_uuid_of(identity))
    return [_to_wire(row) for row in rows]
