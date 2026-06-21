"""Source-shape (pre-mapping) suite: judges a raw chunk in the tenant's vocabulary.

Checks chunk shape BEFORE mapping (D18): required source columns present, value
plausibility (declared pattern), null and row-count plausibility — so the failure
reason is intelligible to the tenant ("expected column ``item_code``, got
``itemcd``") rather than a misleading downstream symptom.

The expected columns DERIVE FROM THE MAPPING'S RENAME MAP by default
(``from_rename``): the rename map is the single statement of what the engine will
read, so a standalone list could only drift. The coupling crosses no lib boundary
— the caller (Slice 10/14) hands the rename map over as a plain dict. A fully
authored standalone definition remains possible.

Extra columns are tolerated by default (the source may carry columns the mapping
ignores; receiver-permissive posture, D13) — set ``allow_extra_columns=False``
for strict vocabularies.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandera.polars as pa
import polars as pl
from pandera import Check
from pandera.api.polars.types import PolarsData
from pydantic import BaseModel, ConfigDict, model_validator

from dis_core.errors import SuiteDefinitionError


class ColumnExpectation(BaseModel):
    """One expected source column, in the tenant's own vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    # Plausibility only (type sniff): a declared regex the non-null values must
    # match. None = presence/null checks only.
    pattern: str | None = None
    # 0.0..1.0; None = no null bound.
    max_null_fraction: float | None = None

    @model_validator(mode="after")
    def _validate(self) -> ColumnExpectation:
        if not self.name:
            raise SuiteDefinitionError("ColumnExpectation.name must be non-empty")
        if self.max_null_fraction is not None and not 0.0 <= self.max_null_fraction <= 1.0:
            raise SuiteDefinitionError(
                f"column {self.name!r}: max_null_fraction must be within [0, 1]",
                column=self.name,
            )
        return self


class SourceShapeSuiteDef(BaseModel):
    """A source-shape suite definition (a raw spec; no canonical model behind it).

    ``min_rows`` defaults to 1 as a deliberate, declarable second line of defence:
    an empty/headers-only chunk fails unless the caller explicitly declares
    ``min_rows=0``. The default is FAIL-LOUDER-ONLY — it can only refuse a chunk,
    never admit or alter wrong data — which is why it is acceptable as a lib
    default where the locale separators (whose defaulting could silently produce
    wrong values) are mandatory declarations. The empty-file product rule's
    first line of defence is tier-0 structural validation at the upload endpoint
    (decisions.md D51); this bound is the pipeline-side backstop.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_columns: tuple[ColumnExpectation, ...]
    min_rows: int = 1
    max_rows: int | None = None
    allow_extra_columns: bool = True

    @model_validator(mode="after")
    def _validate(self) -> SourceShapeSuiteDef:
        if not self.expected_columns:
            raise SuiteDefinitionError("expected_columns must be non-empty")
        names = [column.name for column in self.expected_columns]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise SuiteDefinitionError(f"duplicate expected column(s): {duplicates}")
        if self.min_rows < 0 or (self.max_rows is not None and self.max_rows < self.min_rows):
            raise SuiteDefinitionError(
                f"invalid row bounds: min_rows={self.min_rows}, max_rows={self.max_rows}"
            )
        return self

    @classmethod
    def from_rename(
        cls,
        rename: Mapping[str, str],
        *,
        min_rows: int = 1,
        max_rows: int | None = None,
        allow_extra_columns: bool = True,
        overrides: Mapping[str, ColumnExpectation] | None = None,
    ) -> SourceShapeSuiteDef:
        """Derive the expected columns from a mapping's rename map (its keys).

        ``rename`` is the mapping's source->canonical dict, handed over as a plain
        mapping (no dis-mapping import). ``overrides`` swaps in richer
        expectations (pattern/null bounds) per source column.
        """
        overrides = overrides or {}
        unknown = [name for name in overrides if name not in rename]
        if unknown:
            raise SuiteDefinitionError(
                f"overrides name column(s) {unknown} absent from the rename map",
                column=unknown[0],
            )
        return cls(
            expected_columns=tuple(
                overrides.get(source, ColumnExpectation(name=source)) for source in rename
            ),
            min_rows=min_rows,
            max_rows=max_rows,
            allow_extra_columns=allow_extra_columns,
        )


def _max_null_fraction_check(fraction: float) -> Check:
    def _check(data: PolarsData) -> pl.LazyFrame:
        return data.lazyframe.select((pl.col(data.key).null_count() <= pl.len() * fraction).alias(data.key))

    return Check(_check, name=f"max_null_fraction<={fraction}")


def _row_count_check(min_rows: int, max_rows: int | None) -> Check:
    upper = max_rows if max_rows is not None else None

    def _check(data: PolarsData) -> pl.LazyFrame:
        length = pl.len()
        ok = length >= min_rows if upper is None else (length >= min_rows) & (length <= upper)
        return data.lazyframe.select(ok.alias("row_count"))

    bound = f">={min_rows}" + (f", <={upper}" if upper is not None else "")
    return Check(_check, name=f"row_count{bound}")


def materialize_source_shape(definition: SourceShapeSuiteDef) -> pa.DataFrameSchema:
    """Turn a definition into a runnable Pandera schema (pure; no DB, no config read).

    No dtype enforcement: a raw CSV chunk arrives as strings while other channels
    may hand typed columns — plausibility is the declared ``pattern``, not a dtype.
    """
    columns: dict[str, pa.Column] = {}
    for expectation in definition.expected_columns:
        checks: list[Check] = []
        if expectation.pattern is not None:
            checks.append(Check.str_matches(expectation.pattern))
        if expectation.max_null_fraction is not None:
            checks.append(_max_null_fraction_check(expectation.max_null_fraction))
        columns[expectation.name] = pa.Column(
            None,  # presence + declared plausibility; no dtype check
            checks=checks,
            nullable=True,  # nullability is governed by max_null_fraction, not dtype
            required=True,
            name=expectation.name,
        )
    return pa.DataFrameSchema(
        columns,
        checks=[_row_count_check(definition.min_rows, definition.max_rows)],
        strict=not definition.allow_extra_columns,
        name="source_shape",
    )
