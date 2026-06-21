"""``identity_mirror.tenants`` reads — the in-query tenant predicate chokepoint.

``identity_mirror`` is RLS-OFF (D41): the explicit ``WHERE tenant_id = <token
tenant>`` is the ONLY isolation, exactly the registered ``repos/stores.py``
posture. The single function reads the caller's OWN row (the predicate and the
target are the same id), so no cross-tenant read is even expressible here.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_rls import rls_session
from dis_ui_server.models import TenantRow


async def get_tenant_display_code(engine: AsyncEngine, tenant_id: UUID) -> str | None:
    """The token tenant's ``display_code``, or ``None`` (mirror-NULL or unmirrored).

    Readability only (D52: codes are never a substitute for the UUID), so an
    absent value is not an error — the producer simply omits the optional wire
    field. ``tenant_id`` MUST come from the verified token (``tenant_uuid_of``).
    """
    statement = select(TenantRow.display_code).where(
        TenantRow.tenant_id == tenant_id  # the in-query scoping (D41) — do not remove
    )
    async with rls_session(engine, tenant_id) as conn:
        return (await conn.execute(statement)).scalar_one_or_none()
