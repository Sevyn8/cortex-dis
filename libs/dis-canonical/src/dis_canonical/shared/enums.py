"""Canonical enum and CHECK-vocabulary types, derived from the live schema.

Evidence (introspected from ithina_dis_db, plan mode):
- ``tax_treatment_enum`` pg enum: {INCLUSIVE, EXCLUSIVE}.
- ``expiry_source_enum`` pg enum: {PRINTED, SCANNED, ESTIMATED, CV_DETECTED}.
- ``ck_ssce_event_category_vocab`` CHECK: event_category in {INVENTORY, PRICE,
  COST, REGULATORY, STATUS, CATALOGUE, OTHER}.
- ``ck_ssse_event_subtype_vocab`` CHECK: sale event_subtype in {SALE, RETURN, VOID}.

The two pg enums are modelled as Python ``Enum``; the two CHECK-backed string
vocabularies as ``Literal`` (they are constraints on ``varchar`` columns, not pg
enum types).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class TaxTreatment(StrEnum):
    """``canonical.*.tax_treatment`` — pg ``tax_treatment_enum``."""

    INCLUSIVE = "INCLUSIVE"
    EXCLUSIVE = "EXCLUSIVE"


class ExpirySource(StrEnum):
    """``store_sku_current_position.expiry_source`` — pg ``expiry_source_enum``."""

    PRINTED = "PRINTED"
    SCANNED = "SCANNED"
    ESTIMATED = "ESTIMATED"
    CV_DETECTED = "CV_DETECTED"


# CHECK-constrained varchar vocabularies (not pg enums).
EventCategory = Literal["INVENTORY", "PRICE", "COST", "REGULATORY", "STATUS", "CATALOGUE", "OTHER"]
SaleEventSubtype = Literal["SALE", "RETURN", "VOID"]
