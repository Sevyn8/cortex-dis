"""The derive sub-stage: canonical fields computed from others.

Derive is bounded to the same declarative vocabulary as normalize (slice-05;
arbitrary derive logic is the deferred escape hatch). Each derive target carries
an ORDERED LIST: a generator first (``copy`` / ``constant`` /
``date_from_datetime``), then optional normalize-vocabulary ops — composition
typing is validated at SourceMapping construction, so by the time the engine runs
the chain is type-sound.

Runs AFTER cast, on the typed frame: ``date_from_datetime`` reads a
``Datetime(UTC)`` column (current need: ``event_date``, whose live CHECK pins it
to the source timestamp's UTC date). Generators are total (a null source yields a
null derived cell, not a failure — the source cell's own failure, if any, was
already recorded and drops the row).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from dis_mapping.engine.normalize import apply_transform_list
from dis_mapping.result import CellNormalizationFailure


def _generate(frame: pl.DataFrame, column: str, spec: Any) -> pl.Series:
    """Evaluate a derive generator op into a new Series named ``column``."""
    if spec.op == "copy":
        return frame.get_column(spec.args["source_column"]).rename(column)
    if spec.op == "constant":
        value = spec.args["value"]
        return pl.repeat(value, frame.height, eager=True).rename(column)
    # date_from_datetime — construction validated the source is cast to datetime
    # (UTC), so .dt.date() IS the source timestamp's UTC date (the event_date CHECK).
    source = frame.get_column(spec.args["source_column"])
    return source.dt.date().rename(column)


def run_derive(
    frame: pl.DataFrame,
    derive_rules: Mapping[str, list[Any]],
    failures: list[CellNormalizationFailure],
) -> pl.DataFrame:
    """Run the derive sub-stage: generator first, then the rest of the list in order."""
    for column, specs in derive_rules.items():
        generator, rest = specs[0], specs[1:]
        series = _generate(frame, column, generator)
        if rest:
            # Generator is step 0 of the declared list; the first post-generator
            # op reports transform_index=1 (attributable to its declared position).
            series = apply_transform_list(
                series,
                column,
                rest,
                source_column=generator.args.get("source_column"),
                stage="derive",
                transform_index_offset=1,
                failures=failures,
            )
        frame = frame.with_columns(series)
    return frame
