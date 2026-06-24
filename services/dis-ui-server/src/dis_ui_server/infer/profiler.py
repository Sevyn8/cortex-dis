"""Deterministic server-side CSV profiler for Atlas A3 inference.

Takes a set of example CSVs for one vertical and returns per-column STRUCTURAL
facts only (no LLM): snake_cased name, base type family, a capacity proposal
(varchar bucket / numeric precision-scale), weak nullability, a distinct-value set
for low-cardinality columns (enum candidates), and the header-union with the
section-11-Q5 presence/sample-size stamps. Everything here is reproducible.

Hard rules (the A3 trust split):
- ``produced_by`` is conceptually ``mapping_produced`` for every profiled column
  (the assembler stamps it); A3 never infers a system/compute/enrichment field.
- Capacity (varchar length, numeric precision/scale) is a PROPOSAL at the
  documented default below, not a fact matched against a hand-authored fixture
  (it is declared schema headroom, ratified by a human in A4). The capacity rule
  is: the smallest standard alias whose capacity is >= the observed maximum.

Pure: reads the CSV files handed in; no network, DB, or other I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

# Standard varchar capacities (the dis-canonical Str* aliases) and numeric
# precision/scale capacities (the Numeric* aliases), used by the capacity proposal.
_STR_CAPS: tuple[int, ...] = (32, 64, 128, 256)
_NUMERIC_CAPS: tuple[tuple[int, int], ...] = ((3, 2), (5, 2), (8, 3), (10, 4), (12, 4), (14, 3), (14, 4))
# A string column with at most this many distinct values is proposed as an enum
# candidate (curated, flagged); above it, free text.
_ENUM_MAX_CARDINALITY = 8

_BOOL_TOKENS = frozenset({"true", "false"})
_INT_RE = re.compile(r"^-?\d+$")
_DECIMAL_RE = re.compile(r"^-?\d+\.\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")


@dataclass(frozen=True)
class ProfiledColumn:
    """One union-of-files column's deterministic structural profile."""

    name: str  # snake_cased header
    base: str  # str | decimal | int | date | datetime | bool
    nullable: bool
    max_length: int | None  # varchar capacity bucket (str only)
    precision: int | None  # numeric capacity (decimal only)
    scale: int | None
    distinct_values: tuple[str, ...]  # enum candidate (low-cardinality str), else ()
    present_in_files: int  # N of...
    total_files: int  # ...M
    rows_profiled: int  # sample size (data rows across files where present)
    source_headers: tuple[str, ...]  # the raw headers that unioned to this column


def snake_case(header: str) -> str:
    """Normalize a raw header to a snake_case identifier."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", header.strip()).strip("_").lower()
    return re.sub(r"_+", "_", cleaned)


def bucket_str_capacity(max_len: int) -> int:
    """The smallest standard varchar capacity >= the observed max length (clamp 256)."""
    for cap in _STR_CAPS:
        if max_len <= cap:
            return cap
    return _STR_CAPS[-1]


def bucket_decimal_capacity(precision: int, scale: int) -> tuple[int, int]:
    """The smallest standard (precision, scale) with both >= observed (widest fallback)."""
    for cap_p, cap_s in _NUMERIC_CAPS:
        if cap_p >= precision and cap_s >= scale:
            return (cap_p, cap_s)
    return _NUMERIC_CAPS[-1]


def _is_null(cell: str | None) -> bool:
    return cell is None or cell.strip() == ""


def _decimal_precision_scale(value: str) -> tuple[int, int]:
    """(significant digits, fractional digits) of a decimal literal."""
    digits = value.lstrip("-")
    int_part, _, frac_part = digits.partition(".")
    significant_int = len(int_part.lstrip("0"))
    return significant_int + len(frac_part), len(frac_part)


def _infer_base(values: list[str]) -> tuple[str, int | None, int | None]:
    """Infer (base, precision, scale) over a column's non-null cells. Decimal alone
    carries precision/scale (the proposed capacity)."""
    if not values:
        return ("str", None, None)
    if all(v.lower() in _BOOL_TOKENS for v in values):
        return ("bool", None, None)
    if all(_DATETIME_RE.match(v) for v in values):
        return ("datetime", None, None)
    if all(_DATE_RE.match(v) for v in values):
        return ("date", None, None)
    if all(_INT_RE.match(v) for v in values):
        return ("int", None, None)
    if all(_INT_RE.match(v) or _DECIMAL_RE.match(v) for v in values) and any(
        _DECIMAL_RE.match(v) for v in values
    ):
        prec = 0
        scale = 0
        for v in values:
            if _DECIMAL_RE.match(v):
                p, s = _decimal_precision_scale(v)
                prec = max(prec, p)
                scale = max(scale, s)
        cap_p, cap_s = bucket_decimal_capacity(prec, scale)
        return ("decimal", cap_p, cap_s)
    return ("str", None, None)


def profile_csvs(paths: list[Path]) -> list[ProfiledColumn]:
    """Profile a set of CSVs into union-of-files ProfiledColumns (header order of
    first appearance). Each column is profiled over the files where it appears."""
    total_files = len(paths)
    # Gather, per snake_cased column, the cells and bookkeeping across files.
    order: list[str] = []
    cells: dict[str, list[str | None]] = {}
    raw_headers: dict[str, list[str]] = {}
    present_files: dict[str, int] = {}
    rows_profiled: dict[str, int] = {}
    for path in paths:
        frame = pl.read_csv(path, infer_schema_length=0)
        file_rows = frame.height
        for header in frame.columns:
            name = snake_case(header)
            if name not in cells:
                order.append(name)
                cells[name] = []
                raw_headers[name] = []
                present_files[name] = 0
                rows_profiled[name] = 0
            present_files[name] += 1
            rows_profiled[name] += file_rows
            if header not in raw_headers[name]:
                raw_headers[name].append(header)
            cells[name].extend(frame.get_column(header).to_list())

    profiled: list[ProfiledColumn] = []
    for name in order:
        column_cells = cells[name]
        has_null = any(_is_null(c) for c in column_cells)
        non_null = [c.strip() for c in column_cells if c is not None and c.strip() != ""]
        base, precision, scale = _infer_base(non_null)
        max_length = (
            bucket_str_capacity(max((len(v) for v in non_null), default=0)) if base == "str" else None
        )
        distinct = tuple(sorted(set(non_null)))
        # An enum candidate is a low-cardinality string vocabulary that REPEATS
        # (distinct < rows): an all-distinct column is free text, not a vocabulary.
        # This is a noisy small-sample heuristic, so it is only ever a flagged
        # candidate a human ratifies in A4 (it never decides the type).
        is_enum_candidate = (
            base == "str"
            and 2 <= len(distinct) <= _ENUM_MAX_CARDINALITY
            and len(distinct) < rows_profiled[name]
        )
        nullable = has_null or present_files[name] < total_files
        profiled.append(
            ProfiledColumn(
                name=name,
                base=base,
                nullable=nullable,
                max_length=max_length,
                precision=precision,
                scale=scale,
                distinct_values=distinct if is_enum_candidate else (),
                present_in_files=present_files[name],
                total_files=total_files,
                rows_profiled=rows_profiled[name],
                source_headers=tuple(raw_headers[name]),
            )
        )
    return profiled


def columns_to_payload(columns: list[ProfiledColumn]) -> dict[str, Any]:
    """Serialize profiled columns to the dict handoff the draft-IR assembler consumes."""
    return {
        "columns": [
            {
                "name": c.name,
                "base": c.base,
                "nullable": c.nullable,
                "max_length": c.max_length,
                "precision": c.precision,
                "scale": c.scale,
                "distinct_values": list(c.distinct_values),
                "present_in_files": c.present_in_files,
                "total_files": c.total_files,
                "rows_profiled": c.rows_profiled,
                "source_headers": list(c.source_headers),
            }
            for c in columns
        ]
    }
