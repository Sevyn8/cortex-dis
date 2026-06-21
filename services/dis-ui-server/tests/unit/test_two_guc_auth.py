"""Slice 17b auth-gate unit tests (pure; no DB, no HTTP).

Covers the verifier's reject-on-ambiguous contract (criterion 6), the read-scope
conjunction gate (criteria 3/4), and the impersonation write resolver (criteria 5/9).
The live-RLS row behaviour (a TENANT denied another tenant's row, PLATFORM see-all,
WITH CHECK write-nothing/impersonation) is proven in tests/integration/test_migration_0011.py.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

import pytest

from dis_core.errors import AuthTokenError, OpsRoleRequiredError, TenantScopeError
from dis_ui_server.auth.identity import Identity, UserType
from dis_ui_server.auth.scope import (
    ReadScope,
    WriteScope,
    require_read_scope,
    resolve_acted_for,
)
from dis_ui_server.auth.verifier import verify_token

_TENANT = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees


# --- verify_token: required + explicit user_type, reject-on-ambiguous (criterion 6) ---


def test_valid_tenant_token_yields_tenant_identity(mint_token: Callable[..., str]) -> None:
    ident = verify_token(mint_token(user_type="TENANT", tenant_id=_TENANT, roles=("dis:read",)))
    assert ident.user_type is UserType.TENANT
    assert ident.tenant_id == _TENANT


def test_valid_platform_token_yields_platform_identity(mint_token: Callable[..., str]) -> None:
    ident = verify_token(mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:ops",)))
    assert ident.user_type is UserType.PLATFORM
    assert ident.tenant_id is None


@pytest.mark.parametrize("bad", [None, "", "BOGUS", "platform", "tenant"])
def test_absent_empty_or_unknown_user_type_is_rejected(
    mint_token: Callable[..., str], bad: str | None
) -> None:
    with pytest.raises(AuthTokenError):
        verify_token(mint_token(user_type=bad, tenant_id=_TENANT))


def test_tenant_with_no_tenant_id_is_rejected(mint_token: Callable[..., str]) -> None:
    with pytest.raises(AuthTokenError):
        verify_token(mint_token(user_type="TENANT", tenant_id=None))


def test_platform_with_a_real_tenant_id_claim_is_rejected(mint_token: Callable[..., str]) -> None:
    # Decision 2: a PLATFORM token must be see-all; a real tenant_id claim is incoherent.
    with pytest.raises(AuthTokenError):
        verify_token(mint_token(user_type="PLATFORM", tenant_id=_TENANT, roles=("dis:ops",)))


def test_platform_with_empty_tenant_id_is_accepted_as_see_all(mint_token: Callable[..., str]) -> None:
    # null/empty/absent are equivalent for PLATFORM (decision 2; the minter's choice is not policed).
    ident = verify_token(mint_token(user_type="PLATFORM", tenant_id="", roles=("dis:ops",)))
    assert ident.user_type is UserType.PLATFORM


# --- require_read_scope: PLATFORM see-all requires user_type=PLATFORM AND dis:ops (criteria 3/4) ---


def _ident(user_type: UserType, *, tenant_id: str | None, roles: tuple[str, ...]) -> Identity:
    return Identity(user_id="u", tenant_id=tenant_id, store_id=None, roles=roles, user_type=user_type)


async def test_platform_with_ops_reads_see_all() -> None:
    scope = await require_read_scope(_ident(UserType.PLATFORM, tenant_id=None, roles=("dis:ops", "dis:read")))
    assert scope == ReadScope(is_platform=True, tenant_id=None)


async def test_platform_without_ops_is_denied_see_all() -> None:
    with pytest.raises(OpsRoleRequiredError):
        await require_read_scope(_ident(UserType.PLATFORM, tenant_id=None, roles=("dis:read",)))


async def test_tenant_with_ops_still_reads_only_its_own_tenant() -> None:
    # dis:ops is defense-in-depth; user_type is the discriminator — a TENANT stays pinned.
    scope = await require_read_scope(
        _ident(UserType.TENANT, tenant_id=_TENANT, roles=("dis:ops", "dis:read"))
    )
    assert scope.is_platform is False
    assert scope.tenant_id == UUID(_TENANT)


# --- resolve_acted_for: the impersonation discriminator is the verified user_type (criteria 5/9) ---


def _write_scope(user_type: UserType, *, token_tenant: UUID | None, has_ops: bool) -> WriteScope:
    return WriteScope(user_type=user_type, token_tenant=token_tenant, has_ops=has_ops)


def test_tenant_naming_an_acted_for_tenant_is_rejected() -> None:
    scope = _write_scope(UserType.TENANT, token_tenant=UUID(_TENANT), has_ops=False)
    with pytest.raises(TenantScopeError):
        resolve_acted_for(scope, uuid4())


def test_tenant_without_acted_for_pins_to_its_token_tenant() -> None:
    token_tenant = UUID(_TENANT)
    scope = _write_scope(UserType.TENANT, token_tenant=token_tenant, has_ops=False)
    assert resolve_acted_for(scope, None) == token_tenant


def test_platform_without_ops_cannot_impersonate() -> None:
    scope = _write_scope(UserType.PLATFORM, token_tenant=None, has_ops=False)
    with pytest.raises(OpsRoleRequiredError):
        resolve_acted_for(scope, uuid4())


def test_platform_with_ops_writes_the_request_acted_for_tenant() -> None:
    target = uuid4()
    scope = _write_scope(UserType.PLATFORM, token_tenant=None, has_ops=True)
    assert resolve_acted_for(scope, target) == target


def test_platform_with_ops_but_no_acted_for_tenant_is_rejected() -> None:
    scope = _write_scope(UserType.PLATFORM, token_tenant=None, has_ops=True)
    with pytest.raises(TenantScopeError):
        resolve_acted_for(scope, None)
