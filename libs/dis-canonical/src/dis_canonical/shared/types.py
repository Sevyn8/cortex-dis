"""Constrained scalar types mirroring the live column definitions.

Each alias encodes one ``varchar(n)`` / ``char(n)`` / ``numeric(p,s)`` so the
Pydantic models validate to the same shape the DDL enforces (acceptance: field
types match the DDL). Value-range / cross-field CHECK constraints (e.g. ``>= 0``,
``unit_sale_price <= unit_retail_price``) are deliberately NOT modelled here —
those belong to the database and to dis-validation (Slice 5).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints

# varchar(n)
Str32 = Annotated[str, StringConstraints(max_length=32)]
Str64 = Annotated[str, StringConstraints(max_length=64)]
Str128 = Annotated[str, StringConstraints(max_length=128)]
Str256 = Annotated[str, StringConstraints(max_length=256)]
# char(3) — currency (ISO-4217 length, fixed)
CurrencyCode = Annotated[str, StringConstraints(min_length=3, max_length=3)]

# numeric(precision, scale) -> Decimal with matching constraints
Numeric12_4 = Annotated[Decimal, Field(max_digits=12, decimal_places=4)]
Numeric14_3 = Annotated[Decimal, Field(max_digits=14, decimal_places=3)]
Numeric14_4 = Annotated[Decimal, Field(max_digits=14, decimal_places=4)]
Numeric10_4 = Annotated[Decimal, Field(max_digits=10, decimal_places=4)]
Numeric8_3 = Annotated[Decimal, Field(max_digits=8, decimal_places=3)]
Numeric5_2 = Annotated[Decimal, Field(max_digits=5, decimal_places=2)]
Numeric3_2 = Annotated[Decimal, Field(max_digits=3, decimal_places=2)]
