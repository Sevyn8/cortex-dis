"""The mechanical fallback matcher: a deterministic name + datatype heuristic.

Server-side relocation of the T11 browser matcher. Pure (no I/O): given the
column profiles and the field catalog, it scores each column against the catalog
keys/display names plus a small retail synonym map, with a bonus when the
inferred datatype is compatible with the candidate field's datatype, and returns
the best catalog key per column with the score as confidence. It emits no
reasoning or alternatives (a mechanical match has none to give). It is the
``source == "fallback"`` producer: used when no Gemini key is set or the model
call fails, so the frontend always receives suggestions.
"""

from __future__ import annotations

import re

from dis_ui_server.schemas.mapping_fields import FieldDatatype, TemplateMappingField
from dis_ui_server.schemas.mapping_suggestions import ColumnProfile, Suggestion

# Normalized column-name synonyms -> catalog key. Normalization drops everything
# but [a-z0-9], so "item_code"/"Item Code"/"itemCode" all key as "itemcode".
_SYNONYMS: dict[str, str] = {
    "itemcode": "sku_id",
    "item": "sku_id",
    "sku": "sku_id",
    "product": "sku_id",
    "productcode": "sku_id",
    "articlenumber": "sku_id",
    "variant": "sku_variant",
    "skuvariant": "sku_variant",
    "qty": "quantity",
    "quantity": "quantity",
    "units": "quantity",
    "unitssold": "quantity",
    "price": "unit_sale_price",
    "unitprice": "unit_sale_price",
    "saleprice": "unit_sale_price",
    "sellprice": "unit_sale_price",
    "amount": "unit_sale_price",
    "retailprice": "unit_retail_price",
    "listprice": "unit_retail_price",
    "txndate": "source_sale_timestamp",
    "date": "source_sale_timestamp",
    "saledate": "source_sale_timestamp",
    "soldat": "source_sale_timestamp",
    "timestamp": "source_sale_timestamp",
    "datetime": "source_sale_timestamp",
    "time": "source_sale_timestamp",
    "txn": "transaction_id",
    "transaction": "transaction_id",
    "transactionid": "transaction_id",
    "terminal": "transaction_id",
    "register": "transaction_id",
    "pos": "transaction_id",
    "posterminal": "transaction_id",
    "currency": "currency",
    "ccy": "currency",
    "description": "product_description",
    "productdescription": "product_description",
}

# Which inferred datatypes (the UI vocabulary) are compatible with a catalog
# field's datatype. Used only as a small tie-breaking bonus, never a hard gate.
_DATATYPE_COMPAT: dict[str, set[FieldDatatype]] = {
    "integer": {"integer", "number"},
    "number": {"number", "integer"},
    "datetime": {"datetime", "date"},
    "date": {"date", "datetime"},
    "text": {"text", "choice"},
    "choice": {"choice", "text"},
}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _name_score(normalized_col: str, field: TemplateMappingField) -> float:
    """Name-similarity score in 0..1 for one column against one catalog field."""
    key_norm = _normalize(field.key)
    display_norm = _normalize(field.display_name)
    if _SYNONYMS.get(normalized_col) == field.key:
        return 0.95
    if normalized_col == key_norm:
        return 0.95
    if normalized_col == display_norm:
        return 0.9
    # Substring either direction, guarded by length so "id" does not match anything.
    if len(normalized_col) >= 3 and (normalized_col in key_norm or key_norm in normalized_col):
        return 0.6
    return 0.0


def _score(profile: ColumnProfile, field: TemplateMappingField) -> float:
    name = _name_score(_normalize(profile.name), field)
    if name <= 0.0:
        return 0.0
    bonus = 0.05 if field.datatype in _DATATYPE_COMPAT.get(profile.inferred_datatype, set()) else 0.0
    return min(1.0, name + bonus)


def match_columns(
    columns: list[ColumnProfile],
    catalog: list[TemplateMappingField],
) -> list[Suggestion]:
    """Best-effort per-column suggestions: argmax catalog key, score as confidence.

    Always returns a valid catalog key per column (the UI's target select then
    always has a matching option); a column with no name signal gets the first
    catalog key at confidence 0.0, which the UI shows as very-low / needs review.
    An empty catalog yields a null target at confidence 0.0.
    """
    suggestions: list[Suggestion] = []
    for profile in columns:
        best_key: str | None = None
        best_score = -1.0
        for field in catalog:
            score = _score(profile, field)
            if score > best_score:
                best_score = score
                best_key = field.key
        # No catalog at all -> null target; otherwise the argmax key (best_key is
        # set whenever the catalog is non-empty, even when every score is 0.0).
        if best_key is None:
            suggestions.append(
                Suggestion(source_column=profile.name, suggested_target=None, confidence=0.0)
            )
        else:
            suggestions.append(
                Suggestion(
                    source_column=profile.name,
                    suggested_target=best_key,
                    confidence=max(0.0, best_score),
                )
            )
    return suggestions
