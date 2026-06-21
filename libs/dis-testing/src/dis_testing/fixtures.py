"""Single source of fixture truth (Slice 2, identity-corrected in Slice 9a).

This module owns the test identity set used by *all* the fakes and seeders:

  * the **seeder** writes these rows (by their internal UUID) into
    ``identity_mirror`` and ``config.source_mappings``;
  * the **Identity Service fake** resolves the external codes and answers with the
    internal UUIDs plus the codes (decisions.md D37/D55);
  * the **Customer Master fake** issues JWTs and ``identity.changed`` events
    carrying the codes / UUIDs per the corrected contracts (D52);
  * the **test Customer Master database** (D48 harness) is seeded from these rows;
  * **tests** bridge code -> internal UUID via :func:`tenant_uuid_for` /
    :func:`store_uuid_for` to read the seeded rows.

Identity model (D37 RESOLVED, D52/D55): the **internal UUID** is the load-bearing
identity end to end. Customer Master's authoritative external codes —
``display_code`` (tenants, e.g. ``buc-ees``) and ``store_code`` (stores, e.g.
``TX-101``) — are readability-only and ride alongside. Both code columns are
nullable at source (D55 as corrected). The fixture set below mirrors the REAL
Customer Master identity set — every tenant and store is coded and ACTIVE; the
nullable-``store_code`` (D55) edge and the INACTIVE-store edge are NOT in this
baseline. They live as scoped, reverted edge fixtures in the tests that need them
(the test-CM stand-in code-less store in ``test_db_pull``; the transient inactive
store in ``test_csv_uploads_live``). The invented ``t_*``/``s_*`` form is retired.

Code uniqueness is load-bearing: the fakes resolve a claim code to exactly one
entity, so the by-code indexes below raise loudly on a duplicate (a silent dict
overwrite would make a fixture unreachable).

The UUIDs below are frozen literal constants (minted once with ``uuid_utils.uuid7()``)
— NOT minted at import. The seeder and the test suite run in separate processes and
must agree on the exact UUIDs, so they cannot be random per-process.

PROVISIONAL JWT/JWKS config (R2): the Customer Master contract is not yet signed
off. The signing algorithm, claim set, issuer, and audience below are built to the
*example* values in ``contracts/identity-service/attribute-needs.md`` and must be
revisited when the CM contract lands. The claim identifier values carry the
display_code/store_code form (not internal UUIDs, not the retired t_*/s_*); the
divergence from attribute-needs.md's stale patterns is registered in
``decisions.md`` for the CM sign-off.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from dis_testing.errors import FixtureError

# ---------------------------------------------------------------------------
# Test signing key (RS256) — TEST ONLY. Committed so the CM fake and any verifier
# (in-process or the dockerised fake) share one deterministic key and the JWKS is
# stable. NEVER used outside tests; carries no real authority.
# ---------------------------------------------------------------------------
TEST_JWT_ALG = "RS256"
TEST_JWT_KID = "dis-cm-fake-key-1"

# PROVISIONAL (R2): confirm on Customer Master contract sign-off.
TEST_JWT_ISSUER = "https://customer-master.local"
TEST_JWT_AUDIENCE = "dis"

TEST_RSA_PRIVATE_KEY_PEM = """\
-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDNF83hGGk1CVHV
7FpLZXaIE5bLoPrfX7fUQLJN7/tONZAnbRN3xZwyTDeUUYOa3dOVdQKaWSOoOiuL
dAVH/kfg95nzwAPArxuEtexMa27UYNcubzx4wn0xbEawM/U9MaFAM7/qztjcDXCq
Y0r/yNVx2OehqoYLQU3Bd0EHrq2or3pTE6WZ9wxZbNWNRPlH2vZ5T/9piZ0N10Qk
kD4hznD6IJgcgfE4l8TJMzdzuhTDlcc0xZ277V7ZBzIjNkyNoE2jAZ1iQE9C9Exc
QCtC2UvUtB3apVhmuUuP9vpAcGiJl4TaKDiQOzx36hoCuTzKwhC37nE7eJZkDmV/
h8SWhLThAgMBAAECggEAARtFAx6DkX2Aswm99bQK90wxrok+LxuW5Vf8+fpOG/rn
SiJleR4Zis1GaUx5ewnLrMK+yT5+0S/WAPFKmllh0JHcflglApO6+ScpshFa5Syn
FNztwcb/Y42nH1Pr+Y+1nMAI4VbFwvcFQJo9owfTPEq2RIZI5X207W39LY0sW6or
EqT2EIylJCGhJfh+jtGwtYI5HuVJC8sahr2FVYrvu5ca04jgRzxhPOAvXZlvq1iK
hootFD0uTuKCpZz8WKhRDAoYAaDA1Nnln7aIgbibjy+ElIjT4qq7a7g9gzUCF6Nh
wVHteFPzTQcvThfGCHUfKN3C2dY0oZ2L87zWRmcl2QKBgQD1FwWB3gmX1WXxOoab
r6OPNVLy+Ma0Ep6StYKUB0CibAMAyO9K0X1o7VYSSku+lqLN9x25vuwQaua8UaDn
rg2uwDlEhBZiQw162tov1WaL9+gu6PrDS2VD7CgbaMkXcYO7fo6stdTY84QvyrYn
Hj78oJOcg5045r1CJUCaFdhh2QKBgQDWOP0F04POhj1dtApb5Etgd1oNhF7mVam6
MznNj+CVQzGcSyYg2nQ+3dkdGm0MkyyqzuraHZQNHgoTlYxDBtDbEahhiN6xaLe2
kye2HUkqn1+7MfJ05GnRo3uNempYipuaAwUb+cjvTvFzB5QqFARR4mgE5fxJ+viF
+yWYR+h+SQKBgQCh+28mX8tTUDSp9BZW+wRMd9+0ufsJtGydZd1BXHG5Z02szSBq
AH60RHfoarYY5pH/Ml2xD6ARUbXhrMl9lalxX5X51Jq+orZcBhzCFHZL97K6njxt
qnzpIUF4rA6LsfhwiLpfJ2XfZUJuG7m7rN/QM4ibntjgbI+VEe3aaKm0MQKBgGic
w9MIi6FbJLSRq01cmwKsxik7ryxEQPJQ+bVMwZuiiKOOfzwj8giRRelUclRlurZe
/YkuUJJnTPxrV2eT+IJCiTu4Hyf7v1tFWWsxuf06fwFnTsOOl65sa3WXhj9e0MXR
G7mhrWJP5tEJrm0uAT4LlkhuF1n5WUv0bVOEKiEhAoGBAIgy5MWXe7rJsz+DfdOB
NIwDIq/h8aJmvfFI3elrY8WaqFWw+zwfqhyNwJb7FyQ9vjmg76KUtjN80ejvtf5R
LkMhpIvZ5LQaN+5sFMoPrP2cfJjZRXTsSbFkq14dre1SsJH3jj2Hl19q1fYP/OSp
ZbJpYsWFDHLKYoXaZG6rx7vE
-----END PRIVATE KEY-----
"""

TEST_RSA_PUBLIC_KEY_PEM = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzRfN4RhpNQlR1exaS2V2
iBOWy6D631+31ECyTe/7TjWQJ20Td8WcMkw3lFGDmt3TlXUCmlkjqDori3QFR/5H
4PeZ88ADwK8bhLXsTGtu1GDXLm88eMJ9MWxGsDP1PTGhQDO/6s7Y3A1wqmNK/8jV
cdjnoaqGC0FNwXdBB66tqK96UxOlmfcMWWzVjUT5R9r2eU//aYmdDddEJJA+Ic5w
+iCYHIHxOJfEyTM3c7oUw5XHNMWdu+1e2QcyIzZMjaBNowGdYkBPQvRMXEArQtlL
1LQd2qVYZrlLj/b6QHBoiZeE2ig4kDs8d+oaArk8ysIQt+5xO3iWZA5lf4fEloS0
4QIDAQAB
-----END PUBLIC KEY-----
"""


# ---------------------------------------------------------------------------
# Fixture data types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TenantFixture:
    """A test tenant: internal UUID + authoritative code + identity_mirror columns."""

    display_code: str  # Customer Master core.tenants.display_code (authoritative)
    uuid: UUID  # identity_mirror.tenants.tenant_id — the load-bearing DB key
    name: str
    status: str  # identity_mirror.tenants vocab: ONBOARDING/TRIAL/ACTIVE/SUSPENDED/TERMINATED
    pc_created_at: datetime
    pc_updated_at: datetime
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"


@dataclass(frozen=True)
class StoreFixture:
    """A test store: internal UUID + authoritative code + identity_mirror columns."""

    store_code: str | None  # Customer Master core.stores.store_code — nullable at source (D55)
    uuid: UUID  # identity_mirror.stores.store_id — the load-bearing DB key
    tenant_display_code: str  # parent tenant's authoritative code
    name: str
    status: str  # identity_mirror.stores vocab: OPENING/ACTIVE/INACTIVE/CLOSED
    country: str
    timezone: str
    currency: str
    tax_treatment: str  # INCLUSIVE / EXCLUSIVE
    pc_created_at: datetime
    pc_updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"


# Fixed lifecycle timestamps (deterministic; no wall-clock at import).
_CREATED = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)
_UPDATED = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# The default fixture set: the REAL Customer Master identity set — 2 tenants
# (buc-ees, zabka-group) and their 6 stores, ALL coded and ACTIVE. The UUIDs are
# the actual Customer Master internal UUIDs (load-bearing). The nullable-store_code
# (D55) and inactive-store edges are NOT in this baseline — they are preserved as
# scoped, reverted edge fixtures in their respective tests (see module docstring).
# Codes follow the live Customer Master style (tenant display_code a kebab slug
# like 'buc-ees'; store_code a short code like 'TX-101').
# ---------------------------------------------------------------------------
TENANTS: tuple[TenantFixture, ...] = (
    TenantFixture(
        display_code="buc-ees",
        uuid=UUID("019e5e3c-b5d3-705f-9002-2451c4ca2626"),
        name="Buc-ee's",
        status="ACTIVE",
        pc_created_at=_CREATED,
        pc_updated_at=_UPDATED,
        metadata={"pii_policy_version": "v1", "region": "us-central"},
    ),
    TenantFixture(
        display_code="zabka-group",
        uuid=UUID("019e5e3c-b5d6-7eed-93f9-3778a7a7a160"),
        name="Żabka Group",
        status="ACTIVE",
        pc_created_at=_CREATED,
        pc_updated_at=_UPDATED,
        metadata={"pii_policy_version": "v1", "region": "eu-central"},
    ),
)

STORES: tuple[StoreFixture, ...] = (
    StoreFixture(
        store_code="TX-101",
        uuid=UUID("019e5e3c-b62e-75e6-ad62-529127ae944a"),
        tenant_display_code="buc-ees",
        name="Buc-ee's #101 New Braunfels",
        status="ACTIVE",
        country="USA",
        timezone="America/Chicago",
        currency="USD",
        tax_treatment="EXCLUSIVE",
        pc_created_at=_CREATED,
        pc_updated_at=_UPDATED,
    ),
    StoreFixture(
        store_code="TX-102",
        uuid=UUID("019e5e3c-b630-7530-871d-a05f2b7122b3"),
        tenant_display_code="buc-ees",
        name="Buc-ee's #102 Luling",
        status="ACTIVE",
        country="USA",
        timezone="America/Chicago",
        currency="USD",
        tax_treatment="EXCLUSIVE",
        pc_created_at=_CREATED,
        pc_updated_at=_UPDATED,
    ),
    StoreFixture(
        store_code="FL-201",
        uuid=UUID("019e5e3c-b632-75d5-a56a-4bf1acb133ce"),
        tenant_display_code="buc-ees",
        name="Buc-ee's #201 Daytona",
        status="ACTIVE",
        country="USA",
        timezone="America/Chicago",
        currency="USD",
        tax_treatment="EXCLUSIVE",
        pc_created_at=_CREATED,
        pc_updated_at=_UPDATED,
    ),
    StoreFixture(
        store_code="K-001",
        uuid=UUID("019e5e3c-b636-731b-8bdd-46dbb9c02769"),
        tenant_display_code="zabka-group",
        name="Żabka K-001 Stare Miasto",
        status="ACTIVE",
        country="Poland",
        timezone="Europe/Warsaw",
        currency="PLN",
        tax_treatment="INCLUSIVE",
        pc_created_at=_CREATED,
        pc_updated_at=_UPDATED,
    ),
    StoreFixture(
        store_code="W-001",
        uuid=UUID("019e5e3c-b633-7344-93c7-83fb205285ea"),
        tenant_display_code="zabka-group",
        name="Żabka W-001 Mokotów",
        status="ACTIVE",
        country="Poland",
        timezone="Europe/Warsaw",
        currency="PLN",
        tax_treatment="INCLUSIVE",
        pc_created_at=_CREATED,
        pc_updated_at=_UPDATED,
    ),
    StoreFixture(
        store_code="W-002",
        uuid=UUID("019e5e3c-b635-7f44-b056-8dbd926f08ab"),
        tenant_display_code="zabka-group",
        name="Żabka W-002 Praga",
        status="ACTIVE",
        country="Poland",
        timezone="Europe/Warsaw",
        currency="PLN",
        tax_treatment="INCLUSIVE",
        pc_created_at=_CREATED,
        pc_updated_at=_UPDATED,
    ),
)

# Primary tenant/store — the ones the contract examples use; the default the fakes
# resolve to when an artifact does not name a specific identity.
PRIMARY_TENANT = TENANTS[0]
PRIMARY_STORE = STORES[0]

# ---------------------------------------------------------------------------
# Default config.source_mappings row (so mapping_version_id FKs resolve in later
# slices' tests). One ACTIVE mapping for the primary tenant.
#
# template_id/template_name (Slice 14a grain): pinned, deterministic — the
# seeder's idempotency and the rekeyed uq_csm_seq_per_source conflict target
# both key on template_id, so a per-run mint would strand duplicates. The
# pinned value follows the fixture convention (UUIDv7-shaped, load-bearing).
# ---------------------------------------------------------------------------
DEFAULT_SOURCE_ID = "manual_csv_upload"
DEFAULT_TEMPLATE_ID = UUID("019e97d0-0000-7000-8000-000000000001")
DEFAULT_TEMPLATE_NAME = "default"
DEFAULT_SOURCE_MAPPING: dict[str, object] = {
    "tenant_display_code": PRIMARY_TENANT.display_code,
    "source_id": DEFAULT_SOURCE_ID,
    "template_id": DEFAULT_TEMPLATE_ID,
    "template_name": DEFAULT_TEMPLATE_NAME,
    # Packet axis (Slice 14d, NOT NULL): this empty default mapping produces no
    # contribution, so the type label is inert; 'sales' matches the migration's
    # backfill of legacy/empty mappings (the default-upload family).
    "template_type": "sales",
    "status": "ACTIVE",
    "mapping_rules": {
        "version": 1,
        "rename": {},
        "normalize": {},
        "cast": {},
        "derive": {},
    },
}


# ---------------------------------------------------------------------------
# Lookups: code -> fixture. Uniqueness is load-bearing (the fakes resolve a code
# to exactly one entity), so duplicates raise at import — never a silent dict
# overwrite that strands a fixture.
# ---------------------------------------------------------------------------
def _index_unique_codes[T](pairs: Iterable[tuple[str, T]], *, kind: str) -> dict[str, T]:
    index: dict[str, T] = {}
    for code, item in pairs:
        if code in index:
            raise FixtureError(f"duplicate fixture {kind} code: {code!r}")
        index[code] = item
    return index


_TENANTS_BY_CODE: dict[str, TenantFixture] = _index_unique_codes(
    ((t.display_code, t) for t in TENANTS), kind="tenant display"
)
# The None-coded store is intentionally absent here: it has no code to resolve by.
_STORES_BY_CODE: dict[str, StoreFixture] = _index_unique_codes(
    ((s.store_code, s) for s in STORES if s.store_code is not None), kind="store"
)


def tenant_by_display_code(display_code: str) -> TenantFixture:
    try:
        return _TENANTS_BY_CODE[display_code]
    except KeyError as exc:
        raise FixtureError(f"unknown fixture tenant display_code: {display_code!r}") from exc


def store_by_store_code(store_code: str) -> StoreFixture:
    try:
        return _STORES_BY_CODE[store_code]
    except KeyError as exc:
        raise FixtureError(f"unknown fixture store store_code: {store_code!r}") from exc


def tenant_uuid_for(display_code: str) -> UUID:
    """Bridge: tenant display_code -> internal UUID the seeder wrote."""
    return tenant_by_display_code(display_code).uuid


def store_uuid_for(store_code: str) -> UUID:
    """Bridge: store_code -> internal UUID the seeder wrote."""
    return store_by_store_code(store_code).uuid


def stores_for_tenant(tenant_display_code: str) -> tuple[StoreFixture, ...]:
    """Every store under the tenant — including any store_code=None store."""
    return tuple(s for s in STORES if s.tenant_display_code == tenant_display_code)


# ---------------------------------------------------------------------------
# JWT claim builder (provisional shape, R2). Used by the CM fake to issue tokens
# and by the resolve_from_token canned answer to stay consistent.
# ---------------------------------------------------------------------------
DEFAULT_USER_ID = "u_acmeuser0001"
DEFAULT_ROLES = ("dis:upload",)


def build_claims(
    tenant: TenantFixture,
    store: StoreFixture | None,
    *,
    user_id: str = DEFAULT_USER_ID,
    roles: tuple[str, ...] = DEFAULT_ROLES,
    issued_at: int,
    expires_at: int,
) -> dict[str, object]:
    """Build the (provisional) Customer Master JWT claim set.

    Claims follow attribute-needs.md §2; ``iat``/``exp`` are passed in so the
    caller controls the clock (no wall-clock here). Identifier claim values carry
    the authoritative external codes (display_code/store_code) — the JWT is an
    external-facing artifact and never carries internal UUIDs. A store with no
    store_code yields a null store claim (same as a tenant-wide user). The real
    CM claim shape is the unsigned CM contract's to define (registered in
    decisions.md).
    """
    claims: dict[str, object] = {
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "sub": user_id,
        "tenant_id": tenant.display_code,
        "store_id": store.store_code if store else None,
        "roles": list(roles),
        "iat": issued_at,
        "exp": expires_at,
    }
    return claims
