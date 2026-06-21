"""CM -> mirror field mapping, row projection, and the async-URL coercion."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from mirror_sync_consumer.pull.reader import CmStore, CmTenant, _async_url
from mirror_sync_consumer.sync.stores import store_params
from mirror_sync_consumer.sync.tenants import tenant_params

_T = datetime(2024, 1, 1, tzinfo=UTC)
_TENANT_UUID = UUID("019e89f9-dbd5-7703-8221-ae6b811599bb")
_STORE_UUID = UUID("019e89f9-dbd5-7703-8221-ae707db9b918")


def test_tenant_from_row_maps_id_to_tenant_id() -> None:
    tenant = CmTenant.from_row(
        {
            "id": _TENANT_UUID,
            "name": "Acme",
            "display_code": "acme-retail",
            "status": "ACTIVE",
            "created_at": _T,
            "updated_at": _T,
            "suspended_at": None,
            "terminated_at": None,
        }
    )
    assert tenant.tenant_id == _TENANT_UUID
    assert tenant_params(tenant) == {
        "tenant_id": _TENANT_UUID,
        "name": "Acme",
        "display_code": "acme-retail",
        "status": "ACTIVE",
        "pc_created_at": _T,
        "pc_updated_at": _T,
        "pc_suspended_at": None,
        "pc_terminated_at": None,
    }


def test_tenant_from_row_copies_null_display_code_faithfully() -> None:
    # D55: the source column is nullable; the projection copies NULL as-is.
    tenant = CmTenant.from_row(
        {
            "id": _TENANT_UUID,
            "name": "Acme",
            "display_code": None,
            "status": "ACTIVE",
            "created_at": _T,
            "updated_at": _T,
            "suspended_at": None,
            "terminated_at": None,
        }
    )
    assert tenant.display_code is None
    assert tenant_params(tenant)["display_code"] is None


def test_store_from_row_maps_id_to_store_id() -> None:
    store = CmStore.from_row(
        {
            "id": _STORE_UUID,
            "tenant_id": _TENANT_UUID,
            "name": "Acme Downtown",
            "store_code": "AC-001",
            "status": "ACTIVE",
            "country": "US",
            "timezone": "America/New_York",
            "currency": "USD",
            "tax_treatment": "EXCLUSIVE",
            "created_at": _T,
            "updated_at": _T,
            "closed_at": None,
        }
    )
    assert store.store_id == _STORE_UUID
    assert store_params(store) == {
        "store_id": _STORE_UUID,
        "tenant_id": _TENANT_UUID,
        "name": "Acme Downtown",
        "store_code": "AC-001",
        "status": "ACTIVE",
        "country": "US",
        "timezone": "America/New_York",
        "currency": "USD",
        "tax_treatment": "EXCLUSIVE",
        "pc_created_at": _T,
        "pc_updated_at": _T,
        "pc_closed_at": None,
    }


def test_store_from_row_copies_null_store_code_faithfully() -> None:
    # D55: store_code is nullable at source; copied as-is, never defaulted.
    store = CmStore.from_row(
        {
            "id": _STORE_UUID,
            "tenant_id": _TENANT_UUID,
            "name": "Acme Downtown",
            "store_code": None,
            "status": "ACTIVE",
            "country": "US",
            "timezone": "America/New_York",
            "currency": "USD",
            "tax_treatment": "EXCLUSIVE",
            "created_at": _T,
            "updated_at": _T,
            "closed_at": None,
        }
    )
    assert store.store_code is None
    assert store_params(store)["store_code"] is None


def test_async_url_coerces_bare_postgresql() -> None:
    assert _async_url("postgresql://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"


def test_async_url_leaves_psycopg_dialect_untouched() -> None:
    url = "postgresql+psycopg://u:p@h:5432/db"
    assert _async_url(url) == url
