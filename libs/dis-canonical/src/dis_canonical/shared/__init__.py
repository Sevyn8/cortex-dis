"""Shared building blocks for canonical models: identifiers, enums, types, base."""

from __future__ import annotations

from dis_canonical.shared.base import CanonicalModel
from dis_canonical.shared.enums import (
    EventCategory,
    ExpirySource,
    SaleEventSubtype,
    TaxTreatment,
)
from dis_canonical.shared.identifiers import (
    MappingVersionId,
    StoreId,
    TenantId,
    TraceId,
)
from dis_canonical.shared.types import (
    CurrencyCode,
    Numeric3_2,
    Numeric5_2,
    Numeric8_3,
    Numeric10_4,
    Numeric12_4,
    Numeric14_3,
    Numeric14_4,
    Str32,
    Str64,
    Str128,
    Str256,
)

__all__ = [
    "CanonicalModel",
    "CurrencyCode",
    "EventCategory",
    "ExpirySource",
    "MappingVersionId",
    "Numeric3_2",
    "Numeric5_2",
    "Numeric8_3",
    "Numeric10_4",
    "Numeric12_4",
    "Numeric14_3",
    "Numeric14_4",
    "SaleEventSubtype",
    "Str32",
    "Str64",
    "Str128",
    "Str256",
    "StoreId",
    "TaxTreatment",
    "TenantId",
    "TraceId",
]
