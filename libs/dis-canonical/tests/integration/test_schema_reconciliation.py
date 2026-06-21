"""Schema reconciliation: canonical models vs the LIVE ithina_dis_db schema.

This is the regression guard for dis-canonical. The unit tests assert a curated
set of fields; they prove the models are internally consistent but not that they
are *column-complete* against the schema (a column missing from both model and
unit test would pass both). This test closes that gap: it pulls the live column
set independently from ``information_schema.columns`` and asserts exact set
equality with ``model_fields`` in BOTH directions, for every canonical model.

dis-canonical exists to track a schema that keeps changing; when a migration adds,
drops, or renames a canonical column, this test fails until the model is updated.

Runs under the local stack (``dis_engine`` skips when Postgres is unreachable, so a
bare ``uv run pytest`` stays green).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, text

from dis_canonical import (
    StoreSkuChangeEvent,
    StoreSkuCurrentPosition,
    StoreSkuSaleEvent,
    StoreSkuSignalHistory,
)

pytestmark = pytest.mark.integration

# Model -> canonical base table. The four canonical base tables (partitions and the
# parallel staging.* mirror are excluded).
MODELS_BY_TABLE = {
    "store_sku_current_position": StoreSkuCurrentPosition,
    "store_sku_sale_events": StoreSkuSaleEvent,
    "store_sku_change_events": StoreSkuChangeEvent,
    "store_sku_signal_history": StoreSkuSignalHistory,
}


def _live_columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'canonical' AND table_name = :t"
            ),
            {"t": table},
        ).scalars()
        return set(rows)


@pytest.mark.parametrize("table,model", list(MODELS_BY_TABLE.items()))
def test_model_matches_live_columns_exactly(table: str, model: type[BaseModel], dis_engine: Engine) -> None:
    db_cols = _live_columns(dis_engine, table)
    assert db_cols, f"no columns found for canonical.{table} — wrong DB or unmigrated?"

    model_fields = set(model.model_fields.keys())
    missing_from_model = db_cols - model_fields
    extra_in_model = model_fields - db_cols

    assert not missing_from_model, (
        f"{model.__name__} is missing columns present in canonical.{table}: {sorted(missing_from_model)}"
    )
    assert not extra_in_model, (
        f"{model.__name__} has fields absent from canonical.{table}: {sorted(extra_in_model)}"
    )
    # Exact set match (count included).
    assert model_fields == db_cols
