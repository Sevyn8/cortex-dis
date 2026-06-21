"""The verified-request identity value (contract §2.1).

DISTINCT from ``dis_core.identity.models.Identity`` (the UUID-typed Customer
Master contract model used by the identity-service client): this dataclass is
the auth seam's wire-opaque view — ``tenant_id`` / ``store_id`` are opaque
strings to handlers (per D37/D52 the real values are internal UUIDs serialized
lowercase, but nothing here parses them), and ``tenant_id is None`` means a
PLATFORM (cross-tenant ops) user. Never import the dis-core model here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UserType(StrEnum):
    """The session posture asserted by the verified token (Slice 17b).

    EXPLICIT — read from the required ``user_type`` claim and validated at
    verification, never derived from ``tenant_id`` presence (that derivation was the
    "looks honoured, isn't" ambiguity this slice removes).
    """

    TENANT = "TENANT"
    PLATFORM = "PLATFORM"


@dataclass(frozen=True)
class Identity:
    """What the verified token asserts about the caller.

    Every field is read from the verified token ONLY (the foundation rule);
    no request body, query param, or unverified header contributes. ``user_type``
    is EXPLICIT (Slice 17b): a required claim, validated at verification. A TENANT
    identity always carries a ``tenant_id``; a PLATFORM identity carries none
    (see-all) and names any acted-for tenant per-request on the write path, gated on
    the verified PLATFORM posture — never from a token claim.
    """

    user_id: str
    tenant_id: str | None
    store_id: str | None
    roles: tuple[str, ...]
    user_type: UserType
