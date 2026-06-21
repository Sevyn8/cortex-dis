"""Unit tests for the internal identifier vocabulary and the D37 resolution."""

from __future__ import annotations

from uuid import UUID

from dis_core import identifiers
from dis_core.identity import models as identity_models


def test_internal_identifiers_are_uuid_and_int() -> None:
    assert identifiers.TenantId is UUID
    assert identifiers.StoreId is UUID
    assert identifiers.TraceId is UUID
    assert identifiers.MappingVersionId is int


def test_external_identity_aliases_are_retired() -> None:
    # D37 RESOLVED (Slice 9a): the identity contract carries the internal UUID;
    # the invented external t_*/s_* string aliases and their patterns are gone.
    # The historical name collision (identifiers.TenantId UUID vs identity
    # models' Annotated[str]) is dissolved — the contract module no longer
    # defines TenantId/StoreId at all.
    assert not hasattr(identity_models, "TenantId")
    assert not hasattr(identity_models, "StoreId")
    assert not hasattr(identity_models, "TENANT_ID_PATTERN")
    assert not hasattr(identity_models, "STORE_ID_PATTERN")


def test_identity_contract_fields_are_uuid_typed() -> None:
    # The contract Identity model carries the load-bearing UUIDs (D37/D52)
    # plus the optional authoritative external codes (D55).
    fields = identity_models.Identity.model_fields
    assert fields["tenant_id"].annotation is UUID
    assert fields["store_id"].annotation is UUID
    assert "display_code" in fields
    assert "store_code" in fields
