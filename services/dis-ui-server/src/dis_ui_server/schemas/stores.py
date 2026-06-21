"""Wire shape for ``GET /stores-onboarded`` (bare array of these; §2.4 no envelope).

Vocabulary note (§2.6 — the BFF owns translation, DB vocab never leaks): the
mirror's CHECK vocabularies (``OPENING|ACTIVE|INACTIVE|CLOSED``,
``INCLUSIVE|EXCLUSIVE``) are served lowercased; the wire ``Literal`` types pin
the translation so a new DB vocab member fails loud here instead of leaking.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

StoreStatus = Literal["opening", "active", "inactive", "closed"]
StoreTaxTreatment = Literal["inclusive", "exclusive"]


class OnboardedStore(BaseModel):
    """One onboarded store, from ``identity_mirror.stores`` (fields per slice 14b)."""

    store_id: str  # internal UUID, lowercase string (opaque to the UI, §2.2)
    name: str
    store_code: str | None  # nullable at source (D55) — served as-is, never invented
    status: StoreStatus
    country: str
    timezone: str
    currency: str  # ISO 4217, e.g. "EUR"
    tax_treatment: StoreTaxTreatment
