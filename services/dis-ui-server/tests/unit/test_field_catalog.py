"""Field catalog (slice 14b b + 14d): type-aware derivation, drift guard, uniform shape.

The endpoint half runs over the UNREACHABLE-DB client — passing proves the
catalog opens no ``rls_session`` and touches no database (acceptance: "no
tenant context required").
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from dis_canonical import StoreSkuChangeEvent, StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_core.errors import FieldCatalogDriftError
from dis_ui_server.catalog import build_field_catalogs
from dis_ui_server.catalog.labels import LABELS, SNAPSHOT_LABELS, CatalogueFieldLabel, FieldLabel
from dis_ui_server.mapping_validation import enrichment_guaranteed_for
from dis_validation import (
    INVENTORY_CHANGE,
    SALES,
    SNAPSHOT,
    mandatory_mapping_produced,
    mapping_produced_columns,
)

TENANT_B = "019e5e3c-b5d6-7eed-93f9-3778a7a7a160"

# The uniform object shape (Slice 14d), in wire order.
_SHAPE = (
    "key",
    "display_name",
    "section",
    "mandatory",
    "constraints",
    "datatype",
    "description",
    "allowed_values",
    "max_length",
    "sink",
)

# -- per-type derivation ----------------------------------------------------------


def test_event_types_cover_exactly_the_mapping_produced_columns() -> None:
    cats = build_field_catalogs()
    assert {e.key for e in cats[SALES]} == set(mapping_produced_columns(StoreSkuSaleEvent)) | {"__ignore__"}
    assert {e.key for e in cats[INVENTORY_CHANGE]} == set(mapping_produced_columns(StoreSkuChangeEvent)) | {
        "__ignore__"
    }


def test_snapshot_covers_mapping_produced_plus_functional_objects() -> None:
    snap = build_field_catalogs()[SNAPSHOT]
    keys = {e.key for e in snap}
    assert keys == set(mapping_produced_columns(StoreSkuCurrentPosition)) | {"__ignore__"}
    # The file-vs-store asymmetry: currency is mapping-produced (file-supplied);
    # tax_treatment is consumer-injected (store-denormalized), so NOT mappable.
    assert "currency" in keys
    assert "tax_treatment" not in keys


def test_consumer_injected_columns_are_never_mappable() -> None:
    keys = {e.key for cat in build_field_catalogs().values() for e in cat}
    for column in ("tenant_id", "store_id", "trace_id", "mapping_version_id", "tax_treatment", "id"):
        assert column not in keys


def test_snapshot_mandatory_is_the_derived_not_null_set() -> None:
    snap = build_field_catalogs()[SNAPSHOT]
    flagged = {e.key for e in snap if e.mandatory}
    # Slice 16i: the mandatory set subtracts the enrichment value-guaranteed columns,
    # so currency (enrichment-supplied value) is NOT mandatory though tax_treatment was
    # never mappable. Slice 16j: product_category and unit_cost became nullable, so they
    # left the required set too (is_required() False). The catalog flag tracks the same
    # derivation the create gate uses — this equality IS the auto-follow proof.
    hot_enrichment = enrichment_guaranteed_for(StoreSkuCurrentPosition)
    assert flagged == set(mandatory_mapping_produced(StoreSkuCurrentPosition, hot_enrichment))
    assert flagged == {
        "sku_id",
        "product_name",
        "current_retail_price",
    }


def test_snapshot_currency_is_present_but_optional() -> None:
    # Slice 16i headline: currency is mappable-but-optional (the lib supplies its value),
    # distinct from tax_treatment which is non-mappable. It must stay a PRESENT catalog
    # entry with mandatory=false, NOT be dropped like an enrichment-produced column.
    snap = {e.key: e for e in build_field_catalogs()[SNAPSHOT]}
    assert "currency" in snap
    assert snap["currency"].mandatory is False


def test_event_mandatory_flags_match_the_live_required_sets() -> None:
    cats = build_field_catalogs()
    for model, key in ((StoreSkuSaleEvent, SALES), (StoreSkuChangeEvent, INVENTORY_CHANGE)):
        flagged = {e.key for e in cats[key] if e.mandatory}
        assert flagged == set(mandatory_mapping_produced(model))


# -- uniform 10-key shape, sink, constraints (Slice 14d) --------------------------


def test_uniform_object_shape_across_every_type() -> None:
    for cat in build_field_catalogs().values():
        for entry in cat:
            assert tuple(entry.model_dump().keys()) == _SHAPE


def test_sink_is_the_canonical_table_per_type() -> None:
    cats = build_field_catalogs()
    # The __ignore__ sentinel (sink None) now rides every set alongside the canonical table.
    assert {e.sink for e in cats[SALES]} == {"canonical.store_sku_sale_events", None}
    assert {e.sink for e in cats[INVENTORY_CHANGE]} == {"canonical.store_sku_change_events", None}
    snap = {e.key: e for e in cats[SNAPSHOT]}
    assert snap["sku_id"].sink == "canonical.store_sku_current_position"
    # The sentinel object has no canonical sink.
    assert snap["__ignore__"].sink is None


def test_constraints_is_null_for_every_field_v1() -> None:
    assert all(e.constraints is None for cat in build_field_catalogs().values() for e in cat)


def test_sentinel_object_on_every_type() -> None:
    cats = build_field_catalogs()
    # The __ignore__ sentinel is appended uniformly to all three field sets.
    for key in (SALES, INVENTORY_CHANGE, SNAPSHOT):
        sentinel = {e.key: e for e in cats[key]}["__ignore__"]
        assert sentinel.section == "system"
        assert sentinel.datatype is None  # the only object with no datatype
        assert sentinel.mandatory is False
        assert sentinel.sink is None


def test_snapshot_sections_are_the_authored_domain_groups() -> None:
    snap = {e.key: e for e in build_field_catalogs()[SNAPSHOT]}
    assert snap["sku_id"].section == "identity"
    assert snap["current_retail_price"].section == "pricing"
    assert snap["stock_qty"].section == "inventory"
    assert snap["expiry_date"].section == "expiry"
    assert snap["sku_status"].section == "regulatory_status"


def test_event_sections_keep_their_wire_grouping() -> None:
    cats = build_field_catalogs()
    # Wire grouping unchanged, plus the appended __ignore__ sentinel (section "system").
    assert {e.section for e in cats[SALES]} == {"sale_event", "system"}
    assert {e.section for e in cats[INVENTORY_CHANGE]} == {"change_event", "system"}


# -- choice vocabularies + structural facts --------------------------------------


def test_choice_fields_carry_vocabularies() -> None:
    cats = build_field_catalogs()
    sale = {e.key: e for e in cats[SALES]}
    assert sale["event_subtype"].datatype == "choice"
    assert sale["event_subtype"].allowed_values == ["SALE", "RETURN", "VOID"]
    snap = {e.key: e for e in cats[SNAPSHOT]}
    assert snap["expiry_source"].datatype == "choice"
    assert snap["expiry_source"].allowed_values == ["PRINTED", "SCANNED", "ESTIMATED", "CV_DETECTED"]
    # sku_status is free-text varchar(32): no DB enum/CHECK, so no allowed_values.
    assert snap["sku_status"].datatype == "text"
    assert snap["sku_status"].allowed_values is None
    assert snap["sku_status"].max_length == 32


def test_snapshot_structural_facts_derive_from_annotations() -> None:
    snap = {e.key: e for e in build_field_catalogs()[SNAPSHOT]}
    assert snap["sku_id"].datatype == "text"
    assert snap["sku_id"].max_length == 128
    assert snap["currency"].datatype == "text"
    assert snap["currency"].max_length == 3  # char(3)
    assert snap["current_retail_price"].datatype == "number"
    assert snap["lead_time_days"].datatype == "integer"
    assert snap["expiry_date"].datatype == "date"
    assert snap["regulatory_flag"].datatype == "boolean"


# -- drift guard (both directions, both packets) ----------------------------------


def test_missing_event_label_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    pruned = {section: dict(labels) for section, labels in LABELS.items()}
    del pruned["sale_event"]["quantity"]
    monkeypatch.setattr("dis_ui_server.catalog.field_catalog.LABELS", pruned)
    with pytest.raises(FieldCatalogDriftError) as excinfo:
        build_field_catalogs()
    assert excinfo.value.missing == ("quantity",)


def test_stale_event_label_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    padded = {section: dict(labels) for section, labels in LABELS.items()}
    padded["change_event"]["renamed_away"] = FieldLabel("Ghost", "No such canonical column.")
    monkeypatch.setattr("dis_ui_server.catalog.field_catalog.LABELS", padded)
    with pytest.raises(FieldCatalogDriftError) as excinfo:
        build_field_catalogs()
    assert excinfo.value.stale == ("renamed_away",)


def test_missing_snapshot_label_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    pruned = dict(SNAPSHOT_LABELS)
    del pruned["stock_qty"]
    monkeypatch.setattr("dis_ui_server.catalog.field_catalog.SNAPSHOT_LABELS", pruned)
    with pytest.raises(FieldCatalogDriftError) as excinfo:
        build_field_catalogs()
    assert excinfo.value.missing == ("stock_qty",)


def test_stale_snapshot_label_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    padded = dict(SNAPSHOT_LABELS)
    padded["renamed_away"] = CatalogueFieldLabel("Ghost", "No such column.", "identity")
    monkeypatch.setattr("dis_ui_server.catalog.field_catalog.SNAPSHOT_LABELS", padded)
    with pytest.raises(FieldCatalogDriftError) as excinfo:
        build_field_catalogs()
    assert excinfo.value.stale == ("renamed_away",)


def test_label_drift_aborts_the_boot_not_just_the_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    pruned = {section: dict(labels) for section, labels in LABELS.items()}
    del pruned["sale_event"]["quantity"]
    monkeypatch.setattr("dis_ui_server.catalog.field_catalog.LABELS", pruned)
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@127.0.0.1:9/ithina_dis_db")
    monkeypatch.setenv("GCS_BUCKET_BRONZE", "ithina-bronze-raw")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dis")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:9")
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:9")

    from dis_ui_server.main import create_app

    with pytest.raises(FieldCatalogDriftError):
        with TestClient(create_app()):
            pass  # startup must never be reached with a drifted catalog


def test_a_model_side_column_gain_without_a_label_aborts() -> None:
    from dis_ui_server.catalog.field_catalog import _event_entries
    from dis_ui_server.mapping_validation import SECTION_BY_MODEL
    from dis_validation.provenance import PROVENANCE, ColumnProvenance

    class GainedSaleEvent(StoreSkuSaleEvent):
        brand_new_col: str | None = None

    parent = PROVENANCE[StoreSkuSaleEvent]
    PROVENANCE[GainedSaleEvent] = ColumnProvenance(
        consumer_injected=parent.consumer_injected,
        db_generated=parent.db_generated,
        compute_owned=parent.compute_owned,
        mapping_produced=parent.mapping_produced | {"brand_new_col"},
    )
    SECTION_BY_MODEL[GainedSaleEvent] = "sale_event"
    try:
        with pytest.raises(FieldCatalogDriftError) as excinfo:
            _event_entries(GainedSaleEvent, "sale_event")
        assert excinfo.value.missing == ("brand_new_col",)
    finally:
        del PROVENANCE[GainedSaleEvent]
        del SECTION_BY_MODEL[GainedSaleEvent]


def test_signal_history_is_outside_the_catalog_universe() -> None:
    from dis_canonical import StoreSkuSignalHistory
    from dis_core.errors import SuiteDriftError
    from dis_ui_server.mapping_validation import EVENT_MODELS

    assert StoreSkuSignalHistory not in EVENT_MODELS
    with pytest.raises(SuiteDriftError):
        mapping_produced_columns(StoreSkuSignalHistory)


# -- the endpoint: type-aware, identical across callers, no tenant context, no DB --


def test_catalog_served_per_type_and_identical_for_every_caller(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    url = "/api/v1/template-mapping-fields?template_type=snapshot"
    tenant_a = client.get(url, headers={"Authorization": f"Bearer {mint_token()}"})
    tenant_b = client.get(url, headers={"Authorization": f"Bearer {mint_token(tenant_id=TENANT_B)}"})
    ops_token = mint_token(tenant_id=None, roles=("dis:ops",), user_type="PLATFORM")
    ops = client.get(url, headers={"Authorization": f"Bearer {ops_token}"})
    assert tenant_a.status_code == tenant_b.status_code == ops.status_code == 200
    # Byte-identical bodies; the client's DB is UNREACHABLE, so 200 proves no
    # rls_session / no DB read is involved.
    assert tenant_a.content == tenant_b.content == ops.content
    body = tenant_a.json()
    assert len(body) == 29  # 28 mapping-produced + __ignore__
    assert all(tuple(obj.keys()) == _SHAPE for obj in body)


def test_event_catalog_served_per_type(client: TestClient, mint_token: Callable[..., str]) -> None:
    auth = {"Authorization": f"Bearer {mint_token()}"}
    sales = client.get("/api/v1/template-mapping-fields?template_type=sales", headers=auth)
    change = client.get("/api/v1/template-mapping-fields?template_type=inventory_change", headers=auth)
    # Each event set ends with the __ignore__ sentinel (section "system").
    assert [e["section"] for e in sales.json()] == ["sale_event"] * 20 + ["system"]
    assert [e["section"] for e in change.json()] == ["change_event"] * 15 + ["system"]


def test_unknown_template_type_is_400(client: TestClient, mint_token: Callable[..., str]) -> None:
    resp = client.get(
        "/api/v1/template-mapping-fields?template_type=bogus",
        headers={"Authorization": f"Bearer {mint_token()}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_template_type"


def test_missing_template_type_is_400(client: TestClient, mint_token: Callable[..., str]) -> None:
    resp = client.get("/api/v1/template-mapping-fields", headers={"Authorization": f"Bearer {mint_token()}"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_template_type"


def test_catalog_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/template-mapping-fields?template_type=snapshot")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_token"
