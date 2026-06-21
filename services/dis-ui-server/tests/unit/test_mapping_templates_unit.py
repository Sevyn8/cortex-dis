"""Mapping-template endpoints, the DB-free half (auth gates, 400-before-write, envelopes).

The client's database is UNREACHABLE: any 4xx asserted here is proven to occur
BEFORE any DB touch (validation-first ordering — a request rejected by the gate
never opens an rls_session, so nothing invalid can be written even in principle).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from dis_core.errors import MappingConfigError
from dis_ui_server.mapping_translation import date_token_to_strptime, translate_columns_to_mapping_rules
from dis_ui_server.repos.mapping_templates import _violates
from dis_ui_server.schemas.mapping_templates import MappingTemplateCreate

TENANT_A = "019e5e3c-b5d3-705f-9002-2451c4ca2626"  # buc-ees


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _valid_create_body() -> dict[str, Any]:
    """The Slice 16a create shape: semantic intent per column, no engine ops."""
    return {
        "source_id": "manual_csv_upload",
        "template_name": "sales",
        "template_type": "sales",
        "columns": [
            {"src_key": "item", "dest_key": "sku_id"},
            {"src_key": " qty ", "dest_key": "quantity_sold"},
        ],
    }


# -- auth gates (tenant from token only; §2.1 posture) ------------------------------


def test_endpoints_require_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/mapping-templates").status_code == 401
    assert client.get("/api/v1/stores-onboarded").status_code == 401
    assert client.post("/api/v1/mapping-templates", json=_valid_create_body()).status_code == 401
    assert (
        client.patch(
            "/api/v1/mapping-templates/019e9804-12ce-7f57-b9c0-eb3c7d0e8609",
            json={"template_name": "x"},
        ).status_code
        == 401
    )


def test_platform_token_is_refused_where_it_must_be(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    # Slice 17b: GET /mapping-templates now SERVES a PLATFORM+dis:ops token (see-all,
    # proven in the integration suite). What STILL refuses a PLATFORM token, both a clean
    # 403 tenant_scope: /stores-onboarded (identity_mirror stays tenant-pinned — D70 not
    # reopened, decision 1) and POST /mapping-templates with NO acting_for_tenant_id (a
    # platform write must name its acted-for tenant).
    ops = _bearer(mint_token(tenant_id=None, roles=("dis:ops",), user_type="PLATFORM"))
    for response in (
        client.get("/api/v1/stores-onboarded", headers=ops),
        client.post("/api/v1/mapping-templates", headers=ops, json=_valid_create_body()),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "tenant_scope"


def test_malformed_tenant_claim_is_403_not_500(client: TestClient, mint_token: Callable[..., str]) -> None:
    # A non-UUID tenant claim must be refused at the seam edge (tenant_uuid_of),
    # never reach a query cast. The envelope must not echo the claim value.
    response = client.get(
        "/api/v1/stores-onboarded",
        headers=_bearer(mint_token(tenant_id="t_not_a_uuid")),
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "tenant_scope"
    assert "t_not_a_uuid" not in response.text


# -- 16c translation (the columns contract -> a mapping_rules document) -------------
#
# These call the translator FUNCTION directly (pure, no HTTP, no DB): a snapshot/sales
# body in, the engine mapping_rules dict out. The semantic-rejection HTTP tests below
# (DB UNREACHABLE) then prove an invalid request is a 4xx BEFORE any rls_session.


def _make_create(columns: list[dict[str, Any]], *, template_type: str = "snapshot") -> MappingTemplateCreate:
    return MappingTemplateCreate(
        source_id="manual_csv_upload", template_name="catalogue", template_type=template_type, columns=columns
    )


def _snapshot_columns(extra: tuple[dict[str, Any], ...] = ()) -> list[dict[str, Any]]:
    """The mandatory snapshot mapping-produced columns (a gate-valid set), plus any extra."""
    return [
        {"src_key": "a", "dest_key": "sku_id"},
        {"src_key": "b", "dest_key": "product_name"},
        {"src_key": "c", "dest_key": "product_category"},
        {"src_key": "d", "dest_key": "current_retail_price", "src_decimal_separator": "."},
        {"src_key": "e", "dest_key": "unit_cost", "src_decimal_separator": "."},
        {"src_key": "f", "dest_key": "currency"},
        *extra,
    ]


def test_translator_builds_rename_normalize_cast_and_drops_ignore() -> None:
    body = _make_create(
        [
            {"src_key": "codice", "dest_key": "sku_id"},  # text -> cast string, no normalize
            {"src_key": "prezzo", "dest_key": "current_retail_price", "src_decimal_separator": ","},
            {"src_key": "scad", "dest_key": "expiry_date", "src_datetime_format": "DD-MM-YYYY"},
            {"src_key": "vol", "dest_key": "__ignore__"},  # dropped entirely
        ]
    )
    rules = translate_columns_to_mapping_rules(body, tenant_id=TENANT_A)

    assert rules["rename"] == {"codice": "sku_id", "prezzo": "current_retail_price", "scad": "expiry_date"}
    # parse op chosen by the TARGET datatype: a date column gets parse_date (never datetime).
    assert rules["normalize"]["expiry_date"] == [{"op": "parse_date", "args": {"format": "%d-%m-%Y"}}]
    assert rules["normalize"]["current_retail_price"] == [
        {"op": "parse_decimal", "args": {"decimal_separator": ",", "thousands_separator": None}}
    ]
    assert rules["cast"]["sku_id"] == {"type": "string"}
    assert rules["cast"]["expiry_date"] == {"type": "date"}
    # Cast precision/scale reflected from the dis-canonical model (internal, not the request).
    assert rules["cast"]["current_retail_price"] == {"type": "decimal", "precision": 12, "scale": 4}
    assert rules["derive"] == {}


@pytest.mark.parametrize(
    ("token", "code"),
    [
        ("DD-MM-YYYY", "%d-%m-%Y"),
        ("DD/MM/YYYY", "%d/%m/%Y"),
        ("MM/DD/YYYY", "%m/%d/%Y"),
        ("YYYY-MM-DD", "%Y-%m-%d"),
        ("DD-MM-YY", "%d-%m-%y"),
    ],
)
def test_each_accepted_date_token_converts(token: str, code: str) -> None:
    assert date_token_to_strptime(token, tenant_id=TENANT_A) == code


def test_unknown_date_token_is_rejected() -> None:
    with pytest.raises(MappingConfigError):
        date_token_to_strptime("YYYY/MM/DD", tenant_id=TENANT_A)  # outside the locked five


def test_absent_thousands_separator_is_an_explicit_null() -> None:
    body = _make_create([{"src_key": "p", "dest_key": "unit_cost", "src_decimal_separator": "."}])
    rules = translate_columns_to_mapping_rules(body, tenant_id=TENANT_A)
    args = rules["normalize"]["unit_cost"][0]["args"]
    assert args == {"decimal_separator": ".", "thousands_separator": None}  # key present, value null


def test_present_thousands_separator_rides_into_the_op() -> None:
    body = _make_create(
        [
            {
                "src_key": "p",
                "dest_key": "current_retail_price",
                "src_decimal_separator": ",",
                "src_thousand_separator": ".",
            }
        ]
    )
    rules = translate_columns_to_mapping_rules(body, tenant_id=TENANT_A)
    assert rules["normalize"]["current_retail_price"][0]["args"] == {
        "decimal_separator": ",",
        "thousands_separator": ".",
    }


def test_percentage_emits_parse_percent_with_separators() -> None:
    body = _make_create(
        [
            {
                "src_key": "conf",
                "dest_key": "expiry_confidence",
                "src_is_percentage": True,
                "src_decimal_separator": ".",
            }
        ]
    )
    rules = translate_columns_to_mapping_rules(body, tenant_id=TENANT_A)
    assert rules["normalize"]["expiry_confidence"] == [
        {"op": "parse_percent", "args": {"decimal_separator": ".", "thousands_separator": None}}
    ]
    assert rules["cast"]["expiry_confidence"] == {"type": "decimal", "precision": 3, "scale": 2}


def test_datetime_target_gets_parse_datetime_with_utc() -> None:
    # source_sale_timestamp is the only mapping-produced DATETIME target (sales model).
    body = _make_create(
        [{"src_key": "ts", "dest_key": "source_sale_timestamp", "src_datetime_format": "YYYY-MM-DD"}],
        template_type="sales",
    )
    rules = translate_columns_to_mapping_rules(body, tenant_id=TENANT_A)
    assert rules["normalize"]["source_sale_timestamp"] == [
        {"op": "parse_datetime", "args": {"format": "%Y-%m-%d", "timezone": "UTC"}}
    ]
    assert rules["cast"]["source_sale_timestamp"] == {"type": "datetime"}


# -- 16c semantic rejections (the gate 4xx, BEFORE any DB — the client's DB is UNREACHABLE) --
#
# A 4xx here proves the gate ran and rejected before any rls_session: a rejected create
# cannot write even in principle (validation-first ordering). MappingConfigError -> 400.


def test_bad_dest_key_is_400_before_any_db(client: TestClient, mint_token: Callable[..., str]) -> None:
    # _valid_create_body() targets "quantity_sold" — not a sales column -> target legality 400.
    response = client.post(
        "/api/v1/mapping-templates", headers=_bearer(mint_token()), json=_valid_create_body()
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "mapping_config"


def test_missing_mandatory_field_is_400_before_any_db(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    body = _make_create([{"src_key": "x", "dest_key": "sku_id"}]).model_dump()  # legal but incomplete
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "mapping_config"


def test_broken_presence_pairing_is_400_before_any_db(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    # All mandatory snapshot columns PLUS promo_identifier without promo_price -> pairing 400.
    body = _make_create(
        _snapshot_columns(({"src_key": "promo", "dest_key": "promo_identifier"},))
    ).model_dump()
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "mapping_config"


def test_unknown_date_token_is_400_before_any_db(client: TestClient, mint_token: Callable[..., str]) -> None:
    # receipt_date is a standalone date target (no presence pairing); a bad token 400s in
    # translation, before the gate and before any DB touch.
    body = _make_create(
        _snapshot_columns(
            ({"src_key": "rec", "dest_key": "receipt_date", "src_datetime_format": "YYYY/MM/DD"},)
        )
    ).model_dump()
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "mapping_config"


# -- 16a shape rejections (4xx ahead of any DB) -------------------------------------


def test_unknown_template_type_is_400(client: TestClient, mint_token: Callable[..., str]) -> None:
    body = _valid_create_body()
    body["template_type"] = "not_a_type"
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 400
    envelope = response.json()["error"]
    assert envelope["code"] == "invalid_template_type"
    assert envelope["details"]["tenant_id"] == TENANT_A


def test_missing_column_key_is_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    headers = _bearer(mint_token())
    no_dest = _valid_create_body()
    no_dest["columns"] = [{"src_key": "item"}]  # dest_key missing
    assert client.post("/api/v1/mapping-templates", headers=headers, json=no_dest).status_code == 422
    no_src = _valid_create_body()
    no_src["columns"] = [{"dest_key": "sku_id"}]  # src_key missing
    assert client.post("/api/v1/mapping-templates", headers=headers, json=no_src).status_code == 422


def test_malformed_declaration_is_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    body = _valid_create_body()
    body["columns"] = [{"src_key": "p", "dest_key": "current_retail_price", "src_decimal_separator": ";"}]
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation"
    assert ";" not in response.text  # 422 strips submitted values


def test_eu_thousands_separator_is_accepted_at_shape_and_non_member_is_422(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    # Slice 16b widens src_thousand_separator to include "." so EU-format numbers
    # (1.234,56) are declarable. The "." is accepted at the SHAPE layer (no 422); the
    # request then reaches the semantic gate — here an incomplete snapshot, so a clean 400
    # (not a 422), proving the declaration itself passed. A genuine non-member ";" is a 422.
    headers = _bearer(mint_token())
    eu = {
        "source_id": "manual_csv_upload",
        "template_name": "catalogue",
        "template_type": "snapshot",
        "columns": [
            {
                "src_key": "prezzovend",
                "dest_key": "current_retail_price",
                "src_decimal_separator": ",",
                "src_thousand_separator": ".",
            }
        ],
    }
    eu_response = client.post("/api/v1/mapping-templates", headers=headers, json=eu)
    assert eu_response.status_code == 400, eu_response.text
    assert eu_response.json()["error"]["code"] == "mapping_config"

    bad = _valid_create_body()
    bad["columns"] = [{"src_key": "p", "dest_key": "current_retail_price", "src_thousand_separator": ";"}]
    response = client.post("/api/v1/mapping-templates", headers=headers, json=bad)
    assert response.status_code == 422
    assert ";" not in response.text  # 422 strips submitted values


def test_unknown_column_key_is_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    # Strict columns (extra="forbid", open Q1): a stray key fails loud, not silently dropped.
    body = _valid_create_body()
    body["columns"] = [{"src_key": "item", "dest_key": "sku_id", "typo_field": "x"}]
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation"


def test_unknown_top_level_key_is_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    # Strict body (extra="forbid"): a stray TOP-LEVEL key fails loud, not silently dropped
    # (pins MappingTemplateCreate.model_config — the column-level guard does not cover it).
    body = _valid_create_body()
    body["mapping_rules"] = {"version": 1}  # the superseded 14b field must NOT be silently accepted
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation"


def test_empty_columns_is_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    body = _valid_create_body()
    body["columns"] = []
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 422


def test_malformed_source_id_is_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    body = _valid_create_body()
    body["source_id"] = "Not Valid!"  # pattern ^[a-z0-9_]{1,128}$
    response = client.post("/api/v1/mapping-templates", headers=_bearer(mint_token()), json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation"
    assert "Not Valid!" not in response.text  # 422 strips submitted values


def test_template_name_bounds_are_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    """``template_name`` is bounded min_length=1 / max_length=200 at the schema
    (schemas/mapping_templates.py): empty and over-long names are both a 422
    request_validation envelope, with the submitted value stripped."""
    headers = _bearer(mint_token())

    body = _valid_create_body()
    body["template_name"] = ""
    empty = client.post("/api/v1/mapping-templates", headers=headers, json=body)
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "request_validation"

    body = _valid_create_body()
    body["template_name"] = "x" * 201
    too_long = client.post("/api/v1/mapping-templates", headers=headers, json=body)
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "request_validation"
    assert "x" * 201 not in too_long.text  # 422 strips submitted values


def test_empty_patch_body_is_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    response = client.patch(
        "/api/v1/mapping-templates/019e9804-12ce-7f57-b9c0-eb3c7d0e8609",
        headers=_bearer(mint_token()),
        json={},
    )
    assert response.status_code == 422


def test_non_uuid_template_id_is_422(client: TestClient, mint_token: Callable[..., str]) -> None:
    response = client.get("/api/v1/mapping-templates/not-a-uuid", headers=_bearer(mint_token()))
    assert response.status_code == 422


# -- the IntegrityError discriminator (rule 6: nothing broader than the named ones) --


def _integrity_error(constraint_name: str | None) -> IntegrityError:
    """A real sqlalchemy IntegrityError wrapping a psycopg-shaped orig."""

    class _Diag:
        pass

    class _FakePgDbError(Exception):
        pass

    orig = _FakePgDbError("violation")
    diag = _Diag()
    diag.constraint_name = constraint_name  # type: ignore[attr-defined]
    orig.diag = diag  # type: ignore[attr-defined]
    return IntegrityError("INSERT ...", {}, orig)


def test_violates_matches_only_the_named_constraint() -> None:
    assert _violates(_integrity_error("ex_csm_template_name_per_source"), "ex_csm_template_name_per_source")
    # A DIFFERENT constraint (the partial-unique ACTIVE index, a CHECK, anything)
    # must NOT match — the repo's except blocks then fall through to a bare
    # `raise`, so the error surfaces as a 500, never a misleading 409/403.
    assert not _violates(_integrity_error("uq_csm_active_per_source"), "ex_csm_template_name_per_source")
    assert not _violates(_integrity_error("ck_csm_status_vocab"), "ex_csm_template_name_per_source")
    assert not _violates(_integrity_error(None), "ex_csm_template_name_per_source")


def test_violates_handles_a_diagless_orig() -> None:
    # An orig with no .diag at all (non-psycopg DBAPI shape): False, fall through.
    class _BareDbError(Exception):
        pass

    assert not _violates(IntegrityError("stmt", {}, _BareDbError()), "ex_csm_template_name_per_source")


# -- the new error-family envelopes (probe routes; §7.4 additions) ------------------


def test_new_error_envelopes(client: TestClient) -> None:
    cases = {
        "/api/v1/probe/raise/resource-not-found": (404, "resource_not_found"),
        "/api/v1/probe/raise/template-name-conflict": (409, "mapping_template_name_conflict"),
        "/api/v1/probe/raise/mapping-state-conflict": (409, "mapping_state_conflict"),
    }
    for path, (status, code) in cases.items():
        response = client.get(path)
        assert response.status_code == status, path
        assert response.json()["error"]["code"] == code
