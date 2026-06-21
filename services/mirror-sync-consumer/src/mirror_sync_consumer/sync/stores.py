"""Store upsert: ``core.stores`` (CM) -> ``identity_mirror.stores`` (DIS).

Conflict target ``(tenant_id, store_id)`` = the live composite PK ``pk_ims`` — the natural
identity, matching how canonical rows are filed and how the composite store FK
``fk_sscp_store`` is shaped (D39). A store never changes tenants (a CM-side sale under a new
tenant creates a *new* store), so the composite key is stable; ``uq_ims_store_id`` remains an
enforced constraint but is not the conflict target. ``mirror_synced_at`` is set only on insert
or a real change, so an unchanged re-run is a no-op (idempotence).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from mirror_sync_consumer.pull.reader import CmStore

STORE_UPSERT = text(
    """
    INSERT INTO identity_mirror.stores
        (store_id, tenant_id, name, store_code, status, country, timezone, currency, tax_treatment,
         pc_created_at, pc_updated_at, pc_closed_at, mirror_synced_at)
    VALUES
        (:store_id, :tenant_id, :name, :store_code, :status, :country, :timezone, :currency, :tax_treatment,
         :pc_created_at, :pc_updated_at, :pc_closed_at, now())
    ON CONFLICT (tenant_id, store_id) DO UPDATE SET
        name          = EXCLUDED.name,
        store_code    = EXCLUDED.store_code,
        status        = EXCLUDED.status,
        country       = EXCLUDED.country,
        timezone      = EXCLUDED.timezone,
        currency      = EXCLUDED.currency,
        tax_treatment = EXCLUDED.tax_treatment,
        pc_created_at = EXCLUDED.pc_created_at,
        pc_updated_at = EXCLUDED.pc_updated_at,
        pc_closed_at  = EXCLUDED.pc_closed_at,
        mirror_synced_at = now()
    WHERE
        identity_mirror.stores.name           IS DISTINCT FROM EXCLUDED.name
        OR identity_mirror.stores.store_code    IS DISTINCT FROM EXCLUDED.store_code
        OR identity_mirror.stores.status        IS DISTINCT FROM EXCLUDED.status
        OR identity_mirror.stores.country       IS DISTINCT FROM EXCLUDED.country
        OR identity_mirror.stores.timezone      IS DISTINCT FROM EXCLUDED.timezone
        OR identity_mirror.stores.currency      IS DISTINCT FROM EXCLUDED.currency
        OR identity_mirror.stores.tax_treatment IS DISTINCT FROM EXCLUDED.tax_treatment
        OR identity_mirror.stores.pc_created_at IS DISTINCT FROM EXCLUDED.pc_created_at
        OR identity_mirror.stores.pc_updated_at IS DISTINCT FROM EXCLUDED.pc_updated_at
        OR identity_mirror.stores.pc_closed_at  IS DISTINCT FROM EXCLUDED.pc_closed_at
    RETURNING (xmax = 0) AS inserted
    """
)


def store_params(store: CmStore) -> dict[str, Any]:
    """Map a CM store to the upsert bind parameters."""
    return {
        "store_id": store.store_id,
        "tenant_id": store.tenant_id,
        "name": store.name,
        "store_code": store.store_code,  # copied as-is; nullable at source (D55)
        "status": store.status,
        "country": store.country,
        "timezone": store.timezone,
        "currency": store.currency,
        "tax_treatment": store.tax_treatment,
        "pc_created_at": store.pc_created_at,
        "pc_updated_at": store.pc_updated_at,
        "pc_closed_at": store.pc_closed_at,
    }
