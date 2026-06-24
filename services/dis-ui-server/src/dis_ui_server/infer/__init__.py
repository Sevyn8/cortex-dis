"""Atlas A3 inference: example CSVs to a draft IR (profiler + proposer).

Deterministic profiling (``profiler``) and LLM proposing (``proposer``) live here,
beside ``suggest/`` and reusing its shared Vertex boundary. Draft-IR assembly,
validation, and YAML emission live in ``tools/codegen`` (the IR's home); the dict
payloads (``columns_to_payload`` / ``proposals_to_payload``) are the in-memory form
of the YAML handoff, so there is no service-to-tool Python dependency.
"""

from __future__ import annotations

from dis_ui_server.infer.profiler import (
    ProfiledColumn,
    bucket_decimal_capacity,
    bucket_str_capacity,
    columns_to_payload,
    profile_csvs,
    snake_case,
)
from dis_ui_server.infer.proposer import (
    ColumnProposal,
    FieldProposer,
    proposals_to_payload,
)

__all__ = [
    "ColumnProposal",
    "FieldProposer",
    "ProfiledColumn",
    "bucket_decimal_capacity",
    "bucket_str_capacity",
    "columns_to_payload",
    "profile_csvs",
    "proposals_to_payload",
    "snake_case",
]
