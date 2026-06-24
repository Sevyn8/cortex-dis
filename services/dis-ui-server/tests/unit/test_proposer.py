"""Layer (b), LLM half: the proposer's pinned-response parsing, the by-construction
hallucination guardrail, and degrade-never-raise.

Reuses the test_mapping_suggestions.py seam pattern: build the proposer over a
configured VertexClient, then override the single ``_call_model`` network seam with a
recorded JSON (clean determinism, no real model) or a raiser (degrade). The
assembly-side assertion (every proposed value lands origin: inferred, curated
flagged) is in tools/codegen's test_draft_ir.py, which owns assembly.
"""

from __future__ import annotations

import json

import anyio

from dis_ui_server.infer.profiler import ProfiledColumn
from dis_ui_server.infer.proposer import FieldProposer
from dis_ui_server.suggest.vertex_client import VertexClient


def _col(name: str, base: str, *, distinct: tuple[str, ...] = ()) -> ProfiledColumn:
    return ProfiledColumn(
        name=name,
        base=base,
        nullable=True,
        max_length=64 if base == "str" else None,
        precision=None,
        scale=None,
        distinct_values=distinct,
        present_in_files=1,
        total_files=1,
        rows_profiled=3,
        source_headers=(name,),
    )


# Messy-ish profiled names so the model proposes canonical normalizations (the thing
# layer (a) deliberately does not exercise).
_PROFILED = [
    _col("item_code", "str"),
    _col("mrp", "decimal"),
    _col("status_flag", "str", distinct=("ACTIVE", "PAUSED")),
]


def _proposer() -> FieldProposer:
    return FieldProposer(VertexClient("a-project", "a-location", model="gemini-2.5-flash"))


def test_parses_proposals_and_drops_hallucinated_columns() -> None:
    proposer = _proposer()
    proposer._call_model = lambda prompt: json.dumps(  # type: ignore[method-assign]
        {
            "proposals": [
                {"source_column": "item_code", "name": "sku_id", "pii": "none", "display_name": "SKU"},
                {"source_column": "mrp", "name": "current_retail_price", "enum_values": None, "pii": "none"},
                {"source_column": "status_flag", "name": "sku_status", "enum_values": ["ACTIVE", "PAUSED"]},
                # GUARDRAIL: a column the profiler never produced -> dropped, cannot create a field.
                {"source_column": "ghost_column", "name": "totally_made_up"},
            ]
        }
    )
    proposals = anyio.run(proposer.propose, _PROFILED)
    by = {p.source_column: p for p in proposals}

    assert set(by) == {"item_code", "mrp", "status_flag"}  # ghost_column dropped
    assert by["item_code"].canonical_name == "sku_id"
    assert by["mrp"].canonical_name == "current_retail_price"
    assert by["status_flag"].enum_values == ("ACTIVE", "PAUSED")
    assert by["item_code"].pii == "none"


def test_degrades_to_no_proposals_when_the_model_raises() -> None:
    proposer = _proposer()

    def _boom(prompt: str) -> str:
        raise RuntimeError("model exploded")

    proposer._call_model = _boom  # type: ignore[method-assign]
    proposals = anyio.run(proposer.propose, _PROFILED)
    assert proposals == []  # never raises; the draft is assembled from the profiler alone


def test_degrades_when_vertex_is_unconfigured() -> None:
    proposer = FieldProposer(VertexClient(None, None, model="gemini-2.5-flash"))
    proposals = anyio.run(proposer.propose, _PROFILED)
    assert proposals == []
