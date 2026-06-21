"""SourceMapping construction validation: config errors fail loud at construction
(code-quality rule 4), never at runtime — including the mandatory separator
declarations (locale is asserted, never inferred) and derive-list composition.
"""

from __future__ import annotations

from typing import Any

import pytest

from dis_core.errors import DisError, MappingConfigError
from dis_mapping import NORMALIZE_OPS, SourceMapping
from dis_mapping.engine.normalize import NORMALIZE_IMPLS


def _mapping(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": 1,
        "rename": {"itemcd": "sku_id", "price": "unit_cost"},
        "normalize": {},
        "cast": {},
        "derive": {},
    }
    base.update(overrides)
    return base


def test_minimal_mapping_with_empty_rule_sets_is_valid() -> None:
    # The live config.source_mappings row carries exactly this shape (all-empty
    # sub-objects) — it must construct.
    mapping = SourceMapping.model_validate(
        {"version": 1, "rename": {}, "normalize": {}, "cast": {}, "derive": {}}
    )
    assert mapping.target_columns == ()


def test_empty_transform_list_is_a_valid_noop() -> None:
    mapping = SourceMapping.model_validate(_mapping(normalize={"sku_id": []}))
    assert mapping.normalize["sku_id"] == []


def test_ordered_multi_transform_list_is_preserved_in_declared_order() -> None:
    mapping = SourceMapping.model_validate(
        _mapping(
            normalize={
                "sku_id": [
                    {"op": "normalize_whitespace", "args": {}},
                    {"op": "normalize_case", "args": {"mode": "upper"}},
                ]
            }
        )
    )
    assert [spec.op for spec in mapping.normalize["sku_id"]] == [
        "normalize_whitespace",
        "normalize_case",
    ]


def test_unknown_normalize_op_fails_at_construction() -> None:
    with pytest.raises(MappingConfigError, match="unknown normalize op 'frobnicate'"):
        SourceMapping.model_validate(_mapping(normalize={"sku_id": [{"op": "frobnicate", "args": {}}]}))


def test_parse_decimal_missing_decimal_separator_fails_at_construction() -> None:
    # Locale rule: separators are MANDATORY declarations, never defaulted.
    with pytest.raises(MappingConfigError, match="missing required arg.*decimal_separator"):
        SourceMapping.model_validate(
            _mapping(normalize={"unit_cost": [{"op": "parse_decimal", "args": {"thousands_separator": "."}}]})
        )


def test_parse_decimal_missing_thousands_separator_fails_at_construction() -> None:
    # Even "no thousands separator" must be DECLARED (explicit null), not omitted.
    with pytest.raises(MappingConfigError, match="missing required arg.*thousands_separator"):
        SourceMapping.model_validate(
            _mapping(normalize={"unit_cost": [{"op": "parse_decimal", "args": {"decimal_separator": ","}}]})
        )


def test_parse_decimal_explicit_null_thousands_separator_is_a_valid_declaration() -> None:
    mapping = SourceMapping.model_validate(
        _mapping(
            normalize={
                "unit_cost": [
                    {
                        "op": "parse_decimal",
                        "args": {"decimal_separator": ".", "thousands_separator": None},
                    }
                ]
            }
        )
    )
    assert mapping.normalize["unit_cost"][0].args["thousands_separator"] is None


def test_parse_decimal_same_separator_for_both_roles_fails() -> None:
    with pytest.raises(MappingConfigError, match="must differ"):
        SourceMapping.model_validate(
            _mapping(
                normalize={
                    "unit_cost": [
                        {
                            "op": "parse_decimal",
                            "args": {"decimal_separator": ",", "thousands_separator": ","},
                        }
                    ]
                }
            )
        )


def test_parse_percent_requires_decimal_and_thousands_separator() -> None:
    # parse_percent takes the same mandatory separator declarations as parse_decimal.
    with pytest.raises(MappingConfigError, match="missing required arg.*decimal_separator"):
        SourceMapping.model_validate(
            _mapping(normalize={"unit_cost": [{"op": "parse_percent", "args": {"thousands_separator": "."}}]})
        )
    with pytest.raises(MappingConfigError, match="missing required arg.*thousands_separator"):
        SourceMapping.model_validate(
            _mapping(normalize={"unit_cost": [{"op": "parse_percent", "args": {"decimal_separator": ","}}]})
        )


def test_parse_percent_explicit_null_thousands_separator_is_a_valid_declaration() -> None:
    mapping = SourceMapping.model_validate(
        _mapping(
            normalize={
                "unit_cost": [
                    {
                        "op": "parse_percent",
                        "args": {"decimal_separator": ".", "thousands_separator": None},
                    }
                ]
            }
        )
    )
    assert mapping.normalize["unit_cost"][0].op == "parse_percent"


def test_parse_integer_separator_is_mandatory() -> None:
    with pytest.raises(MappingConfigError, match="missing required arg.*thousands_separator"):
        SourceMapping.model_validate(_mapping(normalize={"unit_cost": [{"op": "parse_integer", "args": {}}]}))


def test_parse_datetime_requires_timezone_declaration() -> None:
    with pytest.raises(MappingConfigError, match="missing required arg.*timezone"):
        SourceMapping.model_validate(
            _mapping(
                normalize={"unit_cost": [{"op": "parse_datetime", "args": {"format": "%Y-%m-%d %H:%M"}}]}
            )
        )


def test_parse_datetime_null_timezone_requires_offset_bearing_format() -> None:
    with pytest.raises(MappingConfigError, match="never guessed"):
        SourceMapping.model_validate(
            _mapping(
                normalize={
                    "unit_cost": [
                        {
                            "op": "parse_datetime",
                            "args": {"format": "%Y-%m-%d %H:%M", "timezone": None},
                        }
                    ]
                }
            )
        )


def test_parse_datetime_zone_plus_offset_format_is_ambiguous_declaration() -> None:
    with pytest.raises(MappingConfigError, match="declare one, not both"):
        SourceMapping.model_validate(
            _mapping(
                normalize={
                    "unit_cost": [
                        {
                            "op": "parse_datetime",
                            "args": {"format": "%Y-%m-%dT%H:%M%z", "timezone": "UTC"},
                        }
                    ]
                }
            )
        )


def test_unknown_arg_keys_fail_loud() -> None:
    with pytest.raises(MappingConfigError, match="unknown arg"):
        SourceMapping.model_validate(
            _mapping(normalize={"sku_id": [{"op": "normalize_case", "args": {"mode": "upper", "extra": 1}}]})
        )


def test_normalize_key_must_be_a_rename_target() -> None:
    with pytest.raises(MappingConfigError, match="which no rename produces"):
        SourceMapping.model_validate(
            _mapping(normalize={"not_mapped": [{"op": "normalize_whitespace", "args": {}}]})
        )


def test_cast_key_must_be_a_rename_target() -> None:
    with pytest.raises(MappingConfigError, match="which no rename produces"):
        SourceMapping.model_validate(_mapping(cast={"not_mapped": {"type": "integer"}}))


def test_rename_targets_must_be_unique() -> None:
    with pytest.raises(MappingConfigError, match="not unique"):
        SourceMapping.model_validate(_mapping(rename={"a": "sku_id", "b": "sku_id"}))


def test_derive_target_colliding_with_rename_target_fails() -> None:
    with pytest.raises(MappingConfigError, match="collides with a rename target"):
        SourceMapping.model_validate(
            _mapping(derive={"sku_id": [{"op": "constant", "args": {"value": "X"}}]})
        )


def test_derive_list_must_start_with_a_generator() -> None:
    with pytest.raises(MappingConfigError, match="must start with a generator"):
        SourceMapping.model_validate(_mapping(derive={"event_date": []}))
    with pytest.raises(MappingConfigError, match="unknown derive generator"):
        SourceMapping.model_validate(
            _mapping(derive={"event_date": [{"op": "normalize_case", "args": {"mode": "upper"}}]})
        )


def test_date_from_datetime_requires_datetime_cast_source() -> None:
    with pytest.raises(MappingConfigError, match="must be cast to datetime"):
        SourceMapping.model_validate(
            _mapping(
                derive={"event_date": [{"op": "date_from_datetime", "args": {"source_column": "sku_id"}}]}
            )
        )


def test_derive_composition_rejects_string_ops_after_non_string_generator() -> None:
    # date_from_datetime yields a date; normalize-vocabulary ops transform strings.
    with pytest.raises(MappingConfigError, match="transform strings"):
        SourceMapping.model_validate(
            _mapping(
                cast={"unit_cost": {"type": "datetime"}},
                derive={
                    "event_date": [
                        {"op": "date_from_datetime", "args": {"source_column": "unit_cost"}},
                        {"op": "normalize_case", "args": {"mode": "upper"}},
                    ]
                },
            )
        )


def test_derive_composition_allows_string_ops_after_string_generator() -> None:
    mapping = SourceMapping.model_validate(
        _mapping(
            derive={
                "sku_status": [
                    {"op": "copy", "args": {"source_column": "sku_id"}},
                    {"op": "normalize_case", "args": {"mode": "lower"}},
                ]
            }
        )
    )
    assert [spec.op for spec in mapping.derive["sku_status"]] == ["copy", "normalize_case"]


def test_decimal_cast_requires_declared_precision_and_scale() -> None:
    with pytest.raises(MappingConfigError, match="requires declared precision and scale"):
        SourceMapping.model_validate(_mapping(cast={"unit_cost": {"type": "decimal"}}))


def test_non_decimal_cast_rejects_precision() -> None:
    with pytest.raises(MappingConfigError, match="takes no precision/scale"):
        SourceMapping.model_validate(_mapping(cast={"unit_cost": {"type": "integer", "precision": 10}}))


def test_target_columns_are_rename_then_derive_in_declaration_order() -> None:
    mapping = SourceMapping.model_validate(
        _mapping(derive={"currency": [{"op": "constant", "args": {"value": "INR"}}]})
    )
    assert mapping.target_columns == ("sku_id", "unit_cost", "currency")


def test_config_errors_are_dis_errors() -> None:
    # Code-quality: no raw ValueError/RuntimeError; everything roots at DisError.
    with pytest.raises(DisError):
        SourceMapping.model_validate(_mapping(version=0))


def test_vocabulary_spec_and_engine_impls_cannot_drift() -> None:
    # The declared vocabulary (models) and the implementation registry (engine)
    # must carry exactly the same op set, both directions.
    assert set(NORMALIZE_IMPLS) == set(NORMALIZE_OPS)
