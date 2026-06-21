"""Pydantic models for the Identity Service contract.

These mirror the component schemas in
``contracts/identity-service/identity_service.openapi.yaml`` (the authoritative
contract). Field types, required fields, and enum vocabularies are copied from
that file; if the contract changes, these models change with it (additive only,
per the contract's versioning rules).

Note on identifiers: ``tenant_id`` / ``store_id`` are the **internal UUIDs**
(identical to Customer Master ``core.tenants.id`` / ``core.stores.id`` and the
``identity_mirror`` keys) — the load-bearing identity a caller writes downstream
(decisions.md D37/D52). The invented external ``t_*``/``s_*`` form is retired.
Customer Master's authoritative external codes (``display_code``/``store_code``)
ride alongside for readability only (D55); they are never a substitute for the
UUIDs. The same ``TenantId``/``StoreId`` UUID types live in
``dis_core.identifiers`` for DB/RLS/canonical use — the historical name
collision between the two modules is resolved: both are UUIDs now.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Identifier patterns — copied verbatim from the OpenAPI contract. These are the
# genuine Customer Master artifact forms (upload sessions, endpoint configs);
# the retired t_*/s_* identity patterns are gone (D52).
UPLOAD_SESSION_ID_PATTERN = r"^us_[a-z0-9]{12}$"
ENDPOINT_CONFIG_ID_PATTERN = r"^ec_[a-z0-9]{12}$"

# Where an identity/validation answer was sourced from.
# Identity responses use the first three; validate may also use identity_mirror_fallback.
IdentitySource = Literal["cache_fresh", "cache_stale", "customer_master"]
ValidateSource = Literal[
    "cache_fresh",
    "cache_stale",
    "customer_master",
    "identity_mirror_fallback",
]

ErrorCode = Literal[
    "unauthorized",
    "identity_not_found",
    "circuit_open",
    "internal_error",
    "invalid_request",
]


class Identity(BaseModel):
    """Resolved identity returned by the three ``resolve_*`` methods.

    ``tenant_id``/``store_id`` are the internal UUIDs (load-bearing, D37).
    ``display_code``/``store_code`` are Customer Master's authoritative external
    codes (D55): optional in the schema, populated when present in Customer
    Master (``store_code`` is nullable at source), readability only.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    store_id: UUID
    display_code: str | None = None
    store_code: str | None = None
    is_active: bool
    source: IdentitySource
    metadata: dict[str, Any] | None = None
    resolved_at: str | None = None


class ValidateResponse(BaseModel):
    """Existence + active check returned by ``validate``."""

    model_config = ConfigDict(extra="forbid")

    exists: bool
    is_active: bool
    source: ValidateSource


class ResolveFromTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jwt: str


class ResolveFromUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_session_id: Annotated[str, Field(pattern=UPLOAD_SESSION_ID_PATTERN, examples=["us_a1b2c3d4e5f6"])]


class ResolveFromEndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_config_id: Annotated[
        str, Field(pattern=ENDPOINT_CONFIG_ID_PATTERN, examples=["ec_aabbccddeeff"])
    ]


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    store_id: UUID


class Error(BaseModel):
    """Error envelope returned on every non-2xx response."""

    model_config = ConfigDict(extra="forbid")

    error_code: ErrorCode
    message: str
    trace_id: str | None = None
