"""Wire shapes for ``POST /mapping-suggestions`` (LLM mapping-suggestion endpoint).

Request is the client-side column PROFILE (name, inferred datatype, null rate, a
few sample values); response is one suggestion per column, plus a ``source`` flag
(``llm`` when Gemini produced them, ``fallback`` when the mechanical matcher did)
so the UI labels honestly. ``suggested_target`` is always a catalog key or null;
the endpoint validates targets against the field catalog so the model cannot
invent a field. See docs/slices/llm-mapping-suggestion-contract.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Honesty flag: which producer made the suggestions (contract section 2.2).
SuggestionSource = Literal["llm", "fallback"]


class ColumnProfile(BaseModel):
    """One source column's client-side profile (the analyze-step output)."""

    name: str  # header name from the uploaded CSV
    inferred_datatype: str  # UI vocabulary: integer | number | datetime | text | choice
    null_pct: float  # 0..1, share of empty cells in the sampled rows
    sample_values: list[str]  # a few example values


class MappingSuggestionRequest(BaseModel):
    """The parsed profile the frontend sends; scope comes from the token, not here."""

    columns: list[ColumnProfile] = Field(min_length=1)
    source_id: str | None = None  # optional prompt context; advisory, never trusted for scope
    template_name: str | None = None  # optional prompt context
    # Type-aware suggestions (D90): when present + valid, the endpoint scores against THAT
    # type's per-type field catalog (snapshot included). When ABSENT, the endpoint falls back
    # to the legacy sales+inventory_change union (so the not-yet-retired /upload onboarding flow,
    # which sends no template_type, is unchanged). Present + invalid -> 400 invalid_template_type.
    # Tightens to REQUIRED when /upload is retired.
    template_type: str | None = None


class Suggestion(BaseModel):
    """One per-column suggestion. ``suggested_target`` is a catalog key or null."""

    source_column: str
    suggested_target: str | None  # a TemplateMappingField.key, or null for "do not map"
    confidence: float  # 0..1
    reasoning: str | None = None  # optional, LLM path only; never fabricated
    alternatives: list[str] | None = None  # optional ranked catalog keys, LLM path only


class MappingSuggestionResponse(BaseModel):
    """Per-column suggestions plus the honesty flag and (LLM only) the model id."""

    source: SuggestionSource
    model: str | None = None  # the model id when source == "llm"; absent for fallback
    suggestions: list[Suggestion]
