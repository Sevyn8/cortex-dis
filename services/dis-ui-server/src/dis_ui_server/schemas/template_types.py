"""Wire shape for ``GET /template-types`` (bare array; identical for every tenant).

The allowed ``template_type`` values — the packet axis the UI offers the user to
pick before mapping. The KEYS are the single in-code vocabulary
(``dis_validation.TEMPLATE_TYPES``); ``display_name`` / ``description`` are
operator-facing presentation copy, authored in the handler (presentation stays in
the BFF, the vocabulary stays in ``dis-validation``).
"""

from __future__ import annotations

from pydantic import BaseModel


class TemplateType(BaseModel):
    """One selectable template type."""

    key: str  # a member of dis_validation.TEMPLATE_TYPES
    display_name: str
    description: str
