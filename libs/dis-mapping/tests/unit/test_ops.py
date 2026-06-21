"""Per-op behaviour of the bounded normalize vocabulary (slice-05 OQ3).

Every op is atomic and single-purpose; every op passes null through untouched
(that null-passthrough is what makes "a failed cell skips the remaining steps"
hold operationally). Format is asserted by the declaration, never inferred.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl
import pytest

from dis_core.errors import MappingConfigError
from dis_mapping.engine.normalize import NORMALIZE_IMPLS, OpOutcome


def _run(op: str, values: list[str | None], args: Mapping[str, Any]) -> OpOutcome:
    return NORMALIZE_IMPLS[op](pl.Series("col", values, dtype=pl.String), args)


def test_parse_date_reformats_to_iso_and_fails_unparseable() -> None:
    out = _run("parse_date", ["31-12-2025", "2025-12-31", None], {"format": "%d-%m-%Y"})
    assert out.values.to_list() == ["2025-12-31", None, None]
    assert out.failed.to_list() == [False, True, False]  # null input never "fails"


def test_parse_datetime_declared_zone_converts_to_utc_iso() -> None:
    out = _run(
        "parse_datetime",
        ["31-12-2025 23:30", "bogus", None],
        {"format": "%d-%m-%Y %H:%M", "timezone": "Asia/Kolkata"},
    )
    assert out.values.to_list()[0] == "2025-12-31T18:00:00.000000+00:00"  # 23:30 IST
    assert out.failed.to_list() == [False, True, False]


def test_parse_datetime_offset_bearing_format_normalizes_offset_to_utc() -> None:
    out = _run(
        "parse_datetime",
        ["2025-12-31T10:00:00+05:30"],
        {"format": "%Y-%m-%dT%H:%M:%S%z", "timezone": None},
    )
    assert out.values.to_list() == ["2025-12-31T04:30:00.000000+00:00"]
    assert out.failed.to_list() == [False]


def test_parse_datetime_unknown_zone_is_a_config_error_not_a_data_failure() -> None:
    with pytest.raises(MappingConfigError, match="not accepted"):
        _run(
            "parse_datetime",
            ["2025-12-31 10:00"],
            {"format": "%Y-%m-%d %H:%M", "timezone": "Not/AZone"},
        )


def test_parse_decimal_declared_separators_resolve_locale_ambiguity() -> None:
    # "1.299,50" under the EU declaration is 1299.50 — by declaration, not guess.
    eu = _run(
        "parse_decimal",
        ["1.299,50", "23,45", "1,299.50"],
        {"decimal_separator": ",", "thousands_separator": "."},
    )
    assert eu.values.to_list() == ["1299.50", "23.45", None]
    assert eu.failed.to_list() == [False, False, True]

    # The SAME bytes under the US declaration parse the other way round — and the
    # EU-grouped value now fails loud. Both locale directions are covered: each
    # declaration accepts its own grouping and refuses the other's.
    us = _run(
        "parse_decimal",
        ["1,299.50", "1.299,50"],
        {"decimal_separator": ".", "thousands_separator": ","},
    )
    assert us.values.to_list() == ["1299.50", None]
    assert us.failed.to_list() == [False, True]


def test_parse_decimal_misgrouped_thousands_fail_rather_than_misparse() -> None:
    # A declared thousands separator only matches strict 3-digit grouping; a
    # separator anywhere else means the value is not in the declared locale and
    # must fail loud, never silently parse to a wrong number.
    out = _run(
        "parse_decimal",
        ["1.2,5", "12.34", "1.234.5"],
        {"decimal_separator": ",", "thousands_separator": "."},
    )
    assert out.failed.to_list() == [True, True, True]
    assert out.values.to_list() == [None, None, None]


def test_parse_decimal_null_thousands_declaration_means_none_tolerated() -> None:
    out = _run(
        "parse_decimal",
        ["1299.50", "1,299.50"],
        {"decimal_separator": ".", "thousands_separator": None},
    )
    assert out.values.to_list() == ["1299.50", None]
    assert out.failed.to_list() == [False, True]


def test_parse_percent_divides_by_100_under_declared_separators() -> None:
    # US declaration: "%" optional (the declaration marks it a percentage, not the glyph),
    # so a bare "12.5" parses the same as "12.5%". Non-numeric fails loud.
    us = _run(
        "parse_percent",
        ["12.5%", "12.5", "1,299.50%", "nope", None],
        {"decimal_separator": ".", "thousands_separator": ","},
    )
    assert us.values.to_list() == ["0.125", "0.125", "12.995", None, None]
    assert us.failed.to_list() == [False, False, False, True, False]

    # EU declaration: "," decimal, "." thousands — "12,5%" is the same 0.125.
    eu = _run(
        "parse_percent",
        ["12,5%", "1.299,50%"],
        {"decimal_separator": ",", "thousands_separator": "."},
    )
    assert eu.values.to_list() == ["0.125", "12.995"]
    assert eu.failed.to_list() == [False, False]


def test_parse_percent_is_lossless_and_inherits_sign() -> None:
    # The /100 must be EXACT (no float artifact) and a leading sign is inherited from the
    # numeric body pattern, exactly like parse_decimal. "2.37" and "1299.50" are
    # float-HOSTILE: under a Float64/100 path Polars stringifies them as
    # "0.023700000000000002" / "12.995000000000001", so these cases fail unless the divide
    # is done in Decimal. (12.567/-12.5/100 happen to be float-clean — keep but do not rely
    # on them to pin the property.)
    out = _run(
        "parse_percent",
        ["2.37%", "1299.50%", "12.567%", "-12.5%", "100%"],
        {"decimal_separator": ".", "thousands_separator": None},
    )
    assert out.values.to_list() == ["0.0237", "12.995", "0.12567", "-0.125", "1"]
    assert out.failed.to_list() == [False, False, False, False, False]


def test_parse_integer_with_declared_thousands() -> None:
    out = _run("parse_integer", ["1,299", "12.5", None], {"thousands_separator": ","})
    assert out.values.to_list() == ["1299", None, None]
    assert out.failed.to_list() == [False, True, False]


def test_parse_boolean_declared_token_sets() -> None:
    out = _run(
        "parse_boolean",
        ["Y", "N", "maybe", None],
        {"true_values": ["Y", "yes"], "false_values": ["N", "no"]},
    )
    assert out.values.to_list() == ["true", "false", None, None]
    assert out.failed.to_list() == [False, False, True, False]


def test_map_enum_exact_and_case_insensitive() -> None:
    exact = _run("map_enum", ["incl", "INCL"], {"mapping": {"incl": "INCLUSIVE"}})
    assert exact.values.to_list() == ["INCLUSIVE", None]
    assert exact.failed.to_list() == [False, True]

    folded = _run(
        "map_enum",
        ["incl", "INCL", None],
        {"mapping": {"incl": "INCLUSIVE"}, "case_insensitive": True},
    )
    assert folded.values.to_list() == ["INCLUSIVE", "INCLUSIVE", None]
    assert folded.failed.to_list() == [False, False, False]


def test_null_tokens_declares_nulls_without_failing() -> None:
    out = _run("null_tokens", ["N/A", "x", ""], {"tokens": ["N/A", ""]})
    assert out.values.to_list() == [None, "x", None]
    assert out.failed.to_list() == [False, False, False]


def test_normalize_whitespace_trim_and_collapse_flags() -> None:
    both = _run("normalize_whitespace", ["  a   b  "], {})
    assert both.values.to_list() == ["a b"]
    trim_only = _run("normalize_whitespace", ["  a   b  "], {"collapse": False})
    assert trim_only.values.to_list() == ["a   b"]
    collapse_only = _run("normalize_whitespace", ["  a   b  "], {"trim": False})
    assert collapse_only.values.to_list() == [" a b "]


def test_normalize_case_modes() -> None:
    assert _run("normalize_case", ["aB"], {"mode": "upper"}).values.to_list() == ["AB"]
    assert _run("normalize_case", ["aB"], {"mode": "lower"}).values.to_list() == ["ab"]


def test_every_op_passes_null_through_untouched() -> None:
    args_by_op: dict[str, Mapping[str, Any]] = {
        "parse_date": {"format": "%Y-%m-%d"},
        "parse_datetime": {"format": "%Y-%m-%d %H:%M", "timezone": "UTC"},
        "parse_decimal": {"decimal_separator": ".", "thousands_separator": None},
        "parse_percent": {"decimal_separator": ".", "thousands_separator": None},
        "parse_integer": {"thousands_separator": None},
        "parse_boolean": {"true_values": ["y"], "false_values": ["n"]},
        "map_enum": {"mapping": {"a": "A"}},
        "null_tokens": {"tokens": ["N/A"]},
        "normalize_whitespace": {},
        "normalize_case": {"mode": "upper"},
    }
    assert set(args_by_op) == set(NORMALIZE_IMPLS)  # covers the whole vocabulary
    for op, args in args_by_op.items():
        out = _run(op, [None], args)
        assert out.values.to_list() == [None], op
        assert out.failed.to_list() == [False], op
