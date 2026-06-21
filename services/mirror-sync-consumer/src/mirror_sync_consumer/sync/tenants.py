"""Tenant upsert: ``core.tenants`` (CM) -> ``identity_mirror.tenants`` (DIS).

Conflict target ``(tenant_id)`` = the live PK ``pk_imt``. ``mirror_synced_at`` is set to
``now()`` only on insert or a real change (the conditional WHERE), so an unchanged re-run
leaves the row — and its ``mirror_synced_at`` — untouched.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from mirror_sync_consumer.pull.reader import CmTenant

# Business columns compared to decide whether an UPDATE is a real change (mirror_synced_at
# is excluded so it is not what triggers a write).
TENANT_UPSERT = text(
    """
    INSERT INTO identity_mirror.tenants
        (tenant_id, name, display_code, status, pc_created_at, pc_updated_at,
         pc_suspended_at, pc_terminated_at, mirror_synced_at)
    VALUES
        (:tenant_id, :name, :display_code, :status, :pc_created_at, :pc_updated_at,
         :pc_suspended_at, :pc_terminated_at, now())
    ON CONFLICT (tenant_id) DO UPDATE SET
        name             = EXCLUDED.name,
        display_code     = EXCLUDED.display_code,
        status           = EXCLUDED.status,
        pc_created_at    = EXCLUDED.pc_created_at,
        pc_updated_at    = EXCLUDED.pc_updated_at,
        pc_suspended_at  = EXCLUDED.pc_suspended_at,
        pc_terminated_at = EXCLUDED.pc_terminated_at,
        mirror_synced_at = now()
    WHERE
        identity_mirror.tenants.name             IS DISTINCT FROM EXCLUDED.name
        OR identity_mirror.tenants.display_code  IS DISTINCT FROM EXCLUDED.display_code
        OR identity_mirror.tenants.status        IS DISTINCT FROM EXCLUDED.status
        OR identity_mirror.tenants.pc_created_at    IS DISTINCT FROM EXCLUDED.pc_created_at
        OR identity_mirror.tenants.pc_updated_at    IS DISTINCT FROM EXCLUDED.pc_updated_at
        OR identity_mirror.tenants.pc_suspended_at  IS DISTINCT FROM EXCLUDED.pc_suspended_at
        OR identity_mirror.tenants.pc_terminated_at IS DISTINCT FROM EXCLUDED.pc_terminated_at
    RETURNING (xmax = 0) AS inserted
    """
)


def tenant_params(tenant: CmTenant) -> dict[str, Any]:
    """Map a CM tenant to the upsert bind parameters."""
    return {
        "tenant_id": tenant.tenant_id,
        "name": tenant.name,
        "display_code": tenant.display_code,  # copied as-is; nullable at source (D55)
        "status": tenant.status,
        "pc_created_at": tenant.pc_created_at,
        "pc_updated_at": tenant.pc_updated_at,
        "pc_suspended_at": tenant.pc_suspended_at,
        "pc_terminated_at": tenant.pc_terminated_at,
    }
