"""Unit tests for the quarantine console endpoints (slice 15a).

Two halves. PURE: the single crosswalk (display forward == filter reverse, no drift),
the Context composition, the type-tagged id parse, the ISO rendering. WIRE: the parts
of both endpoints that resolve BEFORE any database call - auth/scope (401/403), query
filter validation (422), and a malformed/unknown tagged id (404) - exercised through
the real app over an unreachable DB, so the dependency chain and the envelope handlers
are the real ones. The DB-backed behaviour (isolation, filters, counts, detail) is the
integration suite's job.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from dis_core.errors import TenantScopeError
from dis_core.ids import new_uuid7
from dis_ui_server.auth.scope import ReadScope
from dis_ui_server.handlers.quarantine import _compose_context, _iso, _parse_item_id
from dis_ui_server.models import QuarantinedChunk, QuarantinedRow
from dis_ui_server.repos.quarantine import _list_filters, _tenant_term
from dis_ui_server.schemas.quarantine import (
    StageWire,
    stage_db_values_for,
    stage_to_wire,
    status_db_values_for,
    status_to_wire,
)

# Re-declared locally (the unit suite's convention: no tests package, importlib mode).
TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees

# The live CHECK vocabularies (introspected 15a Task 0): the row 6-member subset plus
# the three chunk-only pre-lookup stages. Every member MUST forward-map (no silent gap).
_ALL_DB_STAGES = (
    "PRE_MAPPING_VALIDATION",
    "MAPPING_EXECUTION",
    "POST_MAPPING_VALIDATION",
    "IDENTITY_VALIDATION",
    "CANONICAL_WRITE",
    "OTHER",
    "PRE_INGEST_PII",
    "BRONZE_WRITE",
    "MAPPING_LOOKUP",
)
_ALL_WIRE_STAGES: tuple[StageWire, ...] = ("source-shape", "canonical-shape", "fk", "normalization", "other")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- tenant predicate is UNCONDITIONAL (pinned independent of RLS) ------------------


@pytest.mark.parametrize("model", [QuarantinedRow, QuarantinedChunk])
def test_list_filters_tenant_predicate_is_conditional_on_platform(
    model: type[QuarantinedRow] | type[QuarantinedChunk],
) -> None:
    # Slice 17b structural catastrophe guard (criterion 7): the tenant predicate is
    # PRESENT for a pinned (TENANT) scope and OMITTED for PLATFORM see-all -- conditioned
    # on is_platform, NEVER on tenant_id being absent. quarantine.* is RLS ON, so this
    # pins the predicate so it cannot quietly vanish for TENANT (leaving isolation on RLS
    # alone) nor wrongly persist for PLATFORM (defeating see-all).
    tenant_id = new_uuid7()
    pinned = _list_filters(
        model,
        ReadScope(is_platform=False, tenant_id=tenant_id),
        source=None,
        stages=None,
        statuses=None,
        cutoff=None,
    )
    assert any("tenant_id" in str(term) for term in pinned), "pinned (TENANT) scope lost its tenant predicate"
    platform = _list_filters(
        model,
        ReadScope(is_platform=True, tenant_id=None),
        source=None,
        stages=None,
        statuses=None,
        cutoff=None,
    )
    assert not any("tenant_id" in str(term) for term in platform), (
        "PLATFORM see-all scope still carries a tenant predicate -> see-all defeated"
    )


@pytest.mark.parametrize("model", [QuarantinedRow, QuarantinedChunk])
def test_tenant_term_refuses_a_pinned_scope_without_a_tenant(
    model: type[QuarantinedRow] | type[QuarantinedChunk],
) -> None:
    # Locks the discriminator as ``scope.is_platform``, NOT tenant-absence: a PINNED
    # (non-platform) scope that somehow lacks a tenant is REFUSED, never silently left
    # unscoped. Catches a mutation that keys the predicate on ``scope.tenant_id is None``
    # (which would drop the predicate for a tenant-less pinned scope instead of raising).
    with pytest.raises(TenantScopeError):
        _tenant_term(model, ReadScope(is_platform=False, tenant_id=None))


# -- crosswalk: ONE source for display AND filter (the no-drift principle) ----------


def test_every_db_stage_forward_maps() -> None:
    # No KeyError -> every live CHECK member is covered; a new member would fail loud.
    assert {stage_to_wire(stage) for stage in _ALL_DB_STAGES} == set(_ALL_WIRE_STAGES)


def test_four_screen_buttons_map_one_to_one() -> None:
    assert stage_to_wire("PRE_MAPPING_VALIDATION") == "source-shape"
    assert stage_to_wire("POST_MAPPING_VALIDATION") == "canonical-shape"
    assert stage_to_wire("IDENTITY_VALIDATION") == "fk"
    assert stage_to_wire("MAPPING_EXECUTION") == "normalization"


def test_other_bucket_collects_the_leftovers_not_drops_them() -> None:
    leftovers = {"CANONICAL_WRITE", "OTHER", "PRE_INGEST_PII", "BRONZE_WRITE", "MAPPING_LOOKUP"}
    assert {stage_to_wire(stage) for stage in leftovers} == {"other"}
    # The reverse (filter) side is the EXACT inverse of the forward (display) side.
    assert set(stage_db_values_for("other")) == leftovers


def test_display_and_filter_read_the_same_crosswalk() -> None:
    # For every wire bucket, the filter's reverse set is exactly the DB stages that
    # forward-map to it: display and filter cannot drift.
    for wire in _ALL_WIRE_STAGES:
        assert set(stage_db_values_for(wire)) == {s for s in _ALL_DB_STAGES if stage_to_wire(s) == wire}


def test_status_crosswalk() -> None:
    assert status_to_wire("NEW") == "open"
    assert status_to_wire("RESOLVED") == "resolved"
    assert status_to_wire("DISMISSED") == "resolved"
    assert status_db_values_for("open") == ["NEW"]
    assert set(status_db_values_for("resolved")) == {"RESOLVED", "DISMISSED"}


def test_unknown_db_member_fails_loud() -> None:
    with pytest.raises(KeyError):
        stage_to_wire("SOME_NEW_STAGE")
    with pytest.raises(KeyError):
        status_to_wire("ARCHIVED")


# -- Context composition (from the row's own failure_context, no second store) ------


def test_context_from_chunk_failure_message() -> None:
    ctx = _compose_context("other", {"failure_message": "mapping config invalid"})
    assert ctx == "other: mapping config invalid"


def test_context_from_row_failures_list() -> None:
    ctx = _compose_context(
        "canonical-shape",
        {"failures": [{"column": "price", "check": "numeric", "reason": "not a number"}]},
    )
    assert ctx.startswith("canonical-shape: ")
    assert "price" in ctx and "numeric" in ctx and "not a number" in ctx


def test_context_falls_back_to_stage_when_empty() -> None:
    assert _compose_context("fk", None) == "fk"
    assert _compose_context("fk", {}) == "fk"


# -- type-tagged id parse -----------------------------------------------------------


def test_parse_item_id_valid() -> None:
    kind, uid = _parse_item_id("row:0190ac0e-1a01-7001-8a01-000000000001")
    assert kind == "row"
    assert str(uid) == "0190ac0e-1a01-7001-8a01-000000000001"
    kind2, _ = _parse_item_id("chunk:0190ac0e-1a01-7001-8a01-000000000002")
    assert kind2 == "chunk"


@pytest.mark.parametrize(
    "bad", ["notatag", "row:not-a-uuid", "bogus:0190ac0e-1a01-7001-8a01-000000000001", ""]
)
def test_parse_item_id_rejects_malformed(bad: str) -> None:
    from dis_core.errors import ResourceNotFoundError

    with pytest.raises(ResourceNotFoundError):
        _parse_item_id(bad)


def test_iso_renders_utc_as_z() -> None:
    assert _iso(datetime(2026, 6, 3, 9, 8, 0, tzinfo=UTC)) == "2026-06-03T09:08:00Z"


# -- WIRE: behaviour that resolves before any DB call -------------------------------


def test_list_requires_a_token(client: TestClient) -> None:
    response = client.get("/api/v1/quarantine")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_token"


def test_list_denies_platform_without_ops(client: TestClient, mint_token: Callable[..., str]) -> None:
    # Slice 17b: GET /quarantine serves a PLATFORM+dis:ops token (see-all, integration
    # suite); a PLATFORM token WITHOUT dis:ops is denied see-all -- a clean 403.
    token = mint_token(user_type="PLATFORM", tenant_id=None, roles=("dis:read",))
    response = client.get("/api/v1/quarantine", headers=_bearer(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ops_role_required"


@pytest.mark.parametrize(
    "query",
    ["status=bogus", "error_type=bogus", "window=bogus", "window=48h", "error_type=PRE_MAPPING_VALIDATION"],
)
def test_list_rejects_bad_filter_values(
    client: TestClient, mint_token: Callable[..., str], query: str
) -> None:
    # error_type takes the WIRE value (source-shape), never the DB enum - so the raw
    # DB member is a 422 too, proving DB vocab cannot leak in through the filter.
    response = client.get(f"/api/v1/quarantine?{query}", headers=_bearer(mint_token(tenant_id=TENANT_A)))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation"


@pytest.mark.parametrize(
    "bad_id", ["notatag", "row:not-a-uuid", "bogus:0190ac0e-1a01-7001-8a01-000000000001"]
)
def test_detail_malformed_id_is_404_before_any_db_call(
    client: TestClient, mint_token: Callable[..., str], bad_id: str
) -> None:
    # The DB is unreachable in unit; a 404 here proves the parse rejects BEFORE the read.
    response = client.get(f"/api/v1/quarantine/{bad_id}", headers=_bearer(mint_token(tenant_id=TENANT_A)))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_detail_requires_a_token(client: TestClient) -> None:
    response = client.get("/api/v1/quarantine/row:0190ac0e-1a01-7001-8a01-000000000001")
    assert response.status_code == 401
