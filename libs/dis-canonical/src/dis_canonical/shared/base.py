"""Shared base model for every canonical row model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CanonicalModel(BaseModel):
    """Base for canonical row models.

    ``extra="forbid"`` so a typo'd or stray field is a validation error rather than
    silently dropped — the canonical contract is closed. ``dis-canonical`` is the
    in-memory representation only; SQL conversion is the consumer's DB layer.
    """

    model_config = ConfigDict(extra="forbid")
