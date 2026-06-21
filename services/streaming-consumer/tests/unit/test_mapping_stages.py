"""AC6: the four sub-stages apply via dis-mapping against the seeded mapping.

One representative op per sub-stage, asserted on the engine output for the
committed sale fixture mapping:

- rename:    ``sku`` -> ``sku_id``
- normalize: ``parse_decimal`` turns the string ``"9.99"`` into canonical form
- cast:      ``quantity`` lands as Decimal(14,3); the timestamp as tz-aware
- derive:    ``event_date`` from the timestamp's UTC date; ``event_subtype`` and
             ``currency`` as constants

The no-escape-hatch property (D61) is review-only — a test cannot prove a
feature's absence — but the import/scope check here backs the review: no module
in the service performs dynamic imports or evals (the registry seam does not
exist).
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from dis_mapping import SourceMapping, apply_mapping

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_SRC = Path(__file__).resolve().parents[2] / "src" / "streaming_consumer"

_CSV = b"sold_at,sku,qty,retail,price,txn,line\n2026-06-05 10:30:00,SKU-9,2,9.99,8.50,T1,1\n"


def _loaded() -> SourceMapping:
    rules = json.loads((_FIXTURES / "mappings" / "sale_pos_v1.json").read_text())
    return SourceMapping.model_validate(rules)


def test_four_sub_stages_apply() -> None:
    frame = pl.read_csv(io.BytesIO(_CSV), infer_schema=False)
    result = apply_mapping(_loaded(), frame)
    assert result.failures == ()
    row = result.contribution.to_dicts()[0]

    assert row["sku_id"] == "SKU-9"  # rename
    assert row["quantity"] == Decimal("2.000")  # normalize(parse_decimal) + cast(14,3)
    assert row["unit_retail_price"] == Decimal("9.9900")  # cast scale 4
    assert row["source_sale_timestamp"] == datetime(2026, 6, 5, 10, 30, tzinfo=UTC)  # cast datetime
    assert row["event_date"] == date(2026, 6, 5)  # derive date_from_datetime
    assert row["event_subtype"] == "SALE"  # derive constant
    assert row["currency"] == "USD"  # derive constant


def test_no_dynamic_transform_seam_in_source() -> None:
    # Backs the D61 review-only property: no importlib / __import__ / eval / exec
    # anywhere in the service source — the named-custom-transform registry seam
    # does not exist.
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        text = path.read_text()
        for marker in ("importlib", "__import__", "eval(", "exec("):
            if marker in text:
                offenders.append(f"{path.name}:{marker}")
    assert offenders == []
