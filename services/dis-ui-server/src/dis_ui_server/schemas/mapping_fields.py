"""Wire shape for ``GET /template-mapping-fields`` (bare array; identical for every tenant).

One entry per (section, canonical column): a column mappable in more than one
template type (e.g. ``sku_id``) appears once per type's field set, because
mandatory-ness and guidance are per target model. ``mandatory`` means "must be
PROVIDED by the template — by rename or by a constant/copy/date_from_datetime
derive", not "a CSV column must point at it".

The object shape is UNIFORM across every template type (Slice 14d): ten keys in
the order below, with JSON ``null`` (never the string ``"null"``) for empty
values. ``section`` is the within-packet grouping label; ``template_type`` is the
PACKET AXIS and is NOT a field key — it parameterises which set is served (the
``?template_type=`` query param). ``sink`` is the one canonical table a field
lands in (``null`` for functional/sentinel objects). ``constraints`` is the
column's constraint or ``null`` (v1: ``null`` for every field — the catalog is
model-derived and the models carry no single-column UNIQUE/CHECK metadata).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Within-packet grouping labels. Event packets keep their existing grouping
# (the wire section == the routed model's label); the catalogue packet groups
# its fields by domain. Every packet also carries `system` (the __ignore__
# sentinel, appended to all field sets).
FieldSection = Literal[
    "sale_event",
    "change_event",
    "identity",
    "product",
    "pricing",
    "inventory",
    "expiry",
    "regulatory_status",
    "system",
]
FieldDatatype = Literal["text", "integer", "number", "date", "datetime", "boolean", "choice", "json"]


class TemplateMappingField(BaseModel):
    """One mappable canonical field, structure derived + labels authored.

    Field order IS the wire order (Slice 14d uniform 10-key shape):
    ``key, display_name, section, mandatory, constraints, datatype, description,
    allowed_values, max_length, sink``.
    """

    key: str  # canonical column name — the exact mapping_rules target
    display_name: str
    section: FieldSection
    mandatory: bool
    constraints: str | None = None  # single-column constraint, or null (v1: always null)
    datatype: FieldDatatype | None = None  # null only for the __ignore__ sentinel
    description: str
    allowed_values: list[str] | None = None  # choice fields only
    max_length: int | None = None  # text fields with a declared cap
    sink: str | None = None  # the canonical table the field lands in; null = functional/sentinel
