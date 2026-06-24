"""Atlas A3 LLM proposer: the inverse of the closed-set mapping suggester.

Where ``GeminiSuggester`` maps a source column to an EXISTING catalog field (closed
target set), this PROPOSES attributes for the canonical fields the deterministic
profiler already found. The model never adds or removes a field and never touches a
system value: the parser is keyed by the profiler's column list, so an item naming a
column the profiler never saw is DROPPED (it cannot introduce a field), and every
applied value is a flagged proposal (the assembler stamps origin: inferred on every
business field regardless of model output).

Reuses the shared Vertex boundary: ``_call_model`` delegates to
``VertexClient.generate_json`` and ``propose`` awaits ``run_blocking(self._call_model,
...)``, so the network seam stays overridable here exactly as in the suggester.
Degrade-never-raise: unconfigured or any failure returns no proposals, and the draft
IR is then assembled from the deterministic profiler alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from dis_core.logging import get_logger
from dis_ui_server.config import SERVICE_NAME
from dis_ui_server.infer.profiler import ProfiledColumn
from dis_ui_server.suggest.vertex_client import VertexClient, run_blocking

_log = get_logger(SERVICE_NAME)

_DEFAULT_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class ColumnProposal:
    """One per-column LLM proposal. All values are flagged (origin: inferred at assembly)."""

    source_column: str  # MUST match a profiled column name, else dropped
    canonical_name: str | None = None  # cross-header name normalization
    enum_values: tuple[str, ...] | None = None  # enum candidate (curated, flagged)
    pii: str | None = None  # PII class candidate (curated, flagged)
    display_name: str | None = None  # A4-era authored metadata
    description: str | None = None


class FieldProposer:
    """Proposes canonical-field attributes via Gemini, degrading to no proposals."""

    def __init__(self, vertex: VertexClient | None, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._vertex = vertex
        self._timeout_s = timeout_s

    async def propose(self, profiled: list[ProfiledColumn]) -> list[ColumnProposal]:
        """Return per-column proposals; never raises. Empty when unconfigured or on any error."""
        if self._vertex is None or not self._vertex.configured:
            return []
        try:
            prompt = self._build_prompt(profiled)
            text = await run_blocking(self._call_model, prompt, timeout_s=self._timeout_s)
            return self._parse_and_validate(text, profiled)
        except Exception as exc:  # timeout, transport, parse, anything: degrade
            _log.bind(stage="atlas_infer", error=type(exc).__name__).warning(
                "field proposal failed; assembling draft from the profiler alone"
            )
            return []

    # -- the single network seam (tests override this) ------------------------------

    def _call_model(self, prompt: str) -> str:
        """Blocking Gemini call; delegates to the shared VertexClient transport."""
        assert self._vertex is not None  # guarded by propose() before this is reached
        return self._vertex.generate_json(prompt)

    # -- pure helpers (directly testable) ------------------------------------------

    def _build_prompt(self, profiled: list[ProfiledColumn]) -> str:
        """Compose the propose-fields prompt: per column, propose a canonical snake_case
        name and (where applicable) an enum vocabulary, a PII class, and authored copy.
        The model PROPOSES attributes for the given columns; it does not invent columns."""
        columns = [
            {
                "source_column": c.name,
                "base_type": c.base,
                "nullable": c.nullable,
                "distinct_values": list(c.distinct_values),  # present only for low-cardinality columns
            }
            for c in profiled
        ]
        instructions = (
            "You are proposing a canonical schema for a data vertical from profiled source "
            "columns. For EACH given source_column propose: a canonical snake_case 'name'; "
            "'enum_values' (a list) ONLY if the column is a closed vocabulary; a 'pii' class "
            "('none' or 'tokenize'); and a short 'display_name' and 'description'. You MUST "
            "only return proposals for the source_column values given; never invent a column. "
            "Every value is a PROPOSAL a human will ratify. Return ONLY JSON of the shape: "
            '{"proposals": [{"source_column": str, "name": str, "enum_values": [str] or null, '
            '"pii": str or null, "display_name": str or null, "description": str or null}]}.'
        )
        return json.dumps({"instructions": instructions, "columns": columns})

    def _parse_and_validate(self, text: str, profiled: list[ProfiledColumn]) -> list[ColumnProposal]:
        """Parse the model JSON; keep ONLY proposals naming a profiled column.

        The guardrail is by construction: ``known`` is the profiler's column set, so an
        item for a column the profiler never produced is dropped (a model cannot
        introduce a field). Returned proposals carry only flagged candidate values; the
        assembler stamps origin: inferred and produced_by: mapping_produced regardless.
        """
        known = {c.name for c in profiled}
        parsed = json.loads(text)
        raw_items = parsed.get("proposals", []) if isinstance(parsed, dict) else []
        proposals: list[ColumnProposal] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            source = item.get("source_column")
            if not isinstance(source, str) or source not in known:
                continue  # DROP: cannot create or rename an unknown column
            name = item.get("name")
            enum_values = item.get("enum_values")
            pii = item.get("pii")
            display_name = item.get("display_name")
            description = item.get("description")
            proposals.append(
                ColumnProposal(
                    source_column=source,
                    canonical_name=name if isinstance(name, str) and name else None,
                    enum_values=tuple(v for v in enum_values if isinstance(v, str))
                    if isinstance(enum_values, list)
                    else None,
                    pii=pii if isinstance(pii, str) and pii else None,
                    display_name=display_name if isinstance(display_name, str) and display_name else None,
                    description=description if isinstance(description, str) and description else None,
                )
            )
        return proposals


def proposals_to_payload(proposals: list[ColumnProposal]) -> dict[str, Any]:
    """Serialize proposals to the dict handoff the draft-IR assembler consumes."""
    return {
        "proposals": [
            {
                "source_column": p.source_column,
                "canonical_name": p.canonical_name,
                "enum_values": list(p.enum_values) if p.enum_values is not None else None,
                "pii": p.pii,
                "display_name": p.display_name,
                "description": p.description,
            }
            for p in proposals
        ]
    }
