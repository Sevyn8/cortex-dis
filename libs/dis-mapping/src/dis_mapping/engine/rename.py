"""The rename sub-stage: source field names -> canonical field names.

Selects ONLY the columns the mapping's rename declares (extra source columns are
the source's business — the source-shape suite governs chunk expectations, D18)
and renames them. A declared source column absent from the chunk is a
caller-contract violation and raises loudly: the source-shape gate runs before
mapping in the pipeline, so reaching the engine with a missing column means the
caller skipped or mis-wired that gate.
"""

from __future__ import annotations

import polars as pl

from dis_core.errors import MappingInputError


def run_rename(frame: pl.DataFrame, rename: dict[str, str]) -> pl.DataFrame:
    """Select the declared source columns and rename them to canonical names."""
    missing = [source for source in rename if source not in frame.columns]
    if missing:
        raise MappingInputError(
            f"chunk is missing source column(s) {missing} declared by the mapping's rename; "
            "the source-shape suite gates chunk shape before mapping (D18)",
            column=missing[0],
        )
    return frame.select(list(rename.keys())).rename(rename)
