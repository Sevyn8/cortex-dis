"""Unit tests for the IR loader: the rev-2 field shape parses as intended."""

from __future__ import annotations

from pathlib import Path

from dis_codegen.ir import ProducedBy, load_ir, parse_ir

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "retail_store_sku_current_position.ir.yaml"


def test_loads_the_retail_fixture() -> None:
    schema = load_ir(_FIXTURE)
    assert schema.vertical == "retail"
    assert schema.schema_version == 3
    assert schema.system_profile == "dis.v1"
    assert len(schema.tables) == 1

    table = schema.tables[0]
    assert table.key == "store_sku_current_position"
    assert table.template_type == "snapshot"
    assert table.semantics == "merge_upsert"
    assert table.sink == "canonical_retail.store_sku_current_position"
    assert table.natural_key == ("store_id", "sku_id", "sku_variant", "sku_lot_batch")
    assert len(table.fields) == 43


def test_named_types_and_enums_parse() -> None:
    schema = load_ir(_FIXTURE)
    assert schema.types["money"].precision == 12
    assert schema.types["money"].scale == 4
    assert schema.types["currency_code"].min_length == 3
    assert schema.types["currency_code"].max_length == 3
    assert schema.enums["tax_treatment"] == ("INCLUSIVE", "EXCLUSIVE")


def test_field_facets_parse() -> None:
    schema = load_ir(_FIXTURE)
    by_name = {f.name: f for f in schema.tables[0].fields}

    assert by_name["id"].produced_by is ProducedBy.DB_GENERATED
    assert by_name["id"].default == "uuidv7()"
    assert by_name["tax_treatment"].produced_by is ProducedBy.ENRICHMENT_PRODUCED
    assert by_name["tax_treatment"].enum_ref == "tax_treatment"
    assert by_name["velocity_7day"].produced_by is ProducedBy.COMPUTE_OWNED
    assert by_name["sku_id"].produced_by is ProducedBy.MAPPING_PRODUCED
    assert by_name["sku_id"].mandatory is True
    assert by_name["regulatory_flag"].default == "false"


def test_inline_type_parses() -> None:
    schema = load_ir(_FIXTURE)
    by_name = {f.name: f for f in schema.tables[0].fields}
    sku_size = by_name["sku_size"]
    assert sku_size.inline_type is not None
    assert sku_size.inline_type.precision == 8
    assert sku_size.inline_type.scale == 3


def test_parse_ir_accepts_a_mapping_directly() -> None:
    schema = parse_ir(
        {
            "vertical": "retail",
            "schema_version": 1,
            "status": "draft",
            "system_profile": "dis.v1",
            "types": {},
            "enums": {},
            "tables": [],
        }
    )
    assert schema.tables == ()
