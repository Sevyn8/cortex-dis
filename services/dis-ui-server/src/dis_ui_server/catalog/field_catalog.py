"""Build the type-aware template-mapping-fields catalog — one canonical truth, built at startup.

STRUCTURAL facts (which columns are mappable, mandatory-ness, datatype, allowed
values, max length) are DERIVED from exactly the sources the create/edit
validator uses: ``dis_validation.mapping_produced_columns`` (the drift-guarded
provenance partition — NOT raw ``model_fields``, which would wrongly expose
consumer-injected columns like ``trace_id`` or ``tax_treatment`` as "mappable")
intersected with the dis-canonical models' field annotations. AUTHORED facts
(display name, description, the catalogue packet's within-section grouping) come
from ``labels.py``, merged by key under a both-directions drift check that raises
``FieldCatalogDriftError`` — the builder runs in the app lifespan, so drift fails
the BOOT loudly (crashloop is the correct misconfiguration signal), never a
half-true catalog at runtime.

Slice 14d makes the catalog TYPE-AWARE: one field set per ``template_type``
(``dis_validation.MODEL_BY_TYPE``). The two event types keep their existing
grouping (section == the routed model's wire label) and gain the two new uniform
keys (``sink``, ``constraints``); the ``snapshot`` (catalogue) type serves the
``store_sku_current_position`` field set grouped by domain. Every type also gains
the ``__ignore__`` sentinel (appended uniformly in ``build_field_catalogs``), so a
tenant can explicitly assign unwanted source columns to it on any template kind.
``sink`` is derived from one per-model constant; ``constraints`` is ``null`` in v1
(the models carry no single-column UNIQUE/CHECK metadata to derive from).

Each per-type catalog is tenant-independent and immutable per process (its inputs
are code constants); built ONCE at startup and served from memory — no
``rls_session``, no DB, no per-request recompute.
"""

from __future__ import annotations

import enum
import types
import typing
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from dis_canonical import StoreSkuChangeEvent, StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_core.errors import FieldCatalogDriftError
from dis_ui_server.catalog.labels import LABELS, SNAPSHOT_LABELS, FieldLabel
from dis_ui_server.mapping_validation import enrichment_guaranteed_for
from dis_ui_server.schemas.mapping_fields import FieldDatatype, FieldSection, TemplateMappingField
from dis_validation import (
    INVENTORY_CHANGE,
    SALES,
    SNAPSHOT,
    mandatory_mapping_produced,
    mapping_produced_columns,
)

# The one canonical table each routed model lands in — the ``sink`` value, derived
# from a single per-model constant (no null fallback; deriving event sinks is clean).
SINK_BY_MODEL: dict[type[BaseModel], str] = {
    StoreSkuSaleEvent: "canonical.store_sku_sale_events",
    StoreSkuChangeEvent: "canonical.store_sku_change_events",
    StoreSkuCurrentPosition: "canonical.store_sku_current_position",
}

# The __ignore__ sentinel: NOT a canonical column (sink null), so it is authored
# whole here rather than schema-derived, and appended to every field set uniformly
# in build_field_catalogs (the suggester's flat universe is left untouched).
_IGNORE_FIELD = TemplateMappingField(
    key="__ignore__",
    display_name="Ignore (do not import)",
    section="system",
    mandatory=False,
    constraints=None,
    datatype=None,
    description=(
        "Assign any source column you do not want imported. More than one column "
        "can map here; all are dropped."
    ),
    allowed_values=None,
    max_length=None,
    sink=None,
)


def _unwrap_optional(annotation: Any) -> Any:
    """Drop ``None`` from ``X | None`` / ``Optional[X]``; return the remaining type."""
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _split_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """Split ``Annotated[base, *meta]`` into (base, meta); plain types pass through."""
    if typing.get_args(annotation) and hasattr(annotation, "__metadata__"):
        args = typing.get_args(annotation)
        return args[0], tuple(args[1:])
    return annotation, ()


def _max_length(metadata: tuple[Any, ...]) -> int | None:
    """The declared max string length, wherever pydantic stashed it."""
    for item in metadata:
        length = getattr(item, "max_length", None)
        if isinstance(length, int):
            return length
    return None


def _meta_int(metadata: tuple[Any, ...], attr: str) -> int | None:
    """Find an int constraint ``attr`` across metadata, descending one level into the
    ``FieldInfo`` wrapper that an Optional field nests its constraint under (required
    fields carry the ``_PydanticGeneralMetadata`` directly; optional fields wrap it)."""
    for item in metadata:
        value = getattr(item, attr, None)
        if isinstance(value, int):
            return value
        nested = getattr(item, "metadata", None)
        if isinstance(nested, list):
            for inner in nested:
                inner_value = getattr(inner, attr, None)
                if isinstance(inner_value, int):
                    return inner_value
    return None


def _max_digits(metadata: tuple[Any, ...]) -> int | None:
    """The declared decimal precision (``Field(max_digits=...)``), wherever stashed."""
    return _meta_int(metadata, "max_digits")


def _decimal_places(metadata: tuple[Any, ...]) -> int | None:
    """The declared decimal scale (``Field(decimal_places=...)``), wherever stashed."""
    return _meta_int(metadata, "decimal_places")


def _datatype_and_values(base: Any) -> tuple[FieldDatatype, list[str] | None]:
    """The friendly datatype label + allowed values for choice/enum types."""
    if typing.get_origin(base) is typing.Literal:
        return "choice", [str(value) for value in typing.get_args(base)]
    if isinstance(base, type) and issubclass(base, enum.Enum):
        return "choice", [str(member.value) for member in base]
    if base is Any or typing.get_origin(base) is dict or base is dict:
        return "json", None
    if isinstance(base, type):
        # bool before int (bool subclasses int); datetime before date likewise.
        if issubclass(base, bool):
            return "boolean", None
        if issubclass(base, int):
            return "integer", None
        if issubclass(base, Decimal):
            return "number", None
        if issubclass(base, datetime):
            return "datetime", None
        if issubclass(base, date):
            return "date", None
        if issubclass(base, str):
            return "text", None
    raise FieldCatalogDriftError(
        f"cannot derive a friendly datatype for annotation {base!r}; the catalog "
        "builder's type dispatch must learn it before the column can be served"
    )


def _assert_no_drift(model: type[BaseModel], produced: frozenset[str], authored: set[str]) -> list[str]:
    """Both-directions label/column drift check; returns the produced keys in
    model declaration order. Raises ``FieldCatalogDriftError`` at boot on drift."""
    derived_keys = [name for name in model.model_fields if name in produced]
    missing = tuple(sorted(set(derived_keys) - authored))
    stale = tuple(sorted(authored - set(derived_keys)))
    if missing or stale:
        raise FieldCatalogDriftError(
            f"field-catalog labels drifted for {model.__name__}: "
            f"missing={list(missing)} stale={list(stale)} — every mapping-produced "
            "column needs exactly one authored label (catalog/labels.py)",
            missing=missing,
            stale=stale,
        )
    return derived_keys


def _structural(model: type[BaseModel], name: str) -> tuple[FieldDatatype, list[str] | None, int | None]:
    """Derive (datatype, allowed_values, max_length) for one model field."""
    field = model.model_fields[name]
    # Constraints may sit on FieldInfo.metadata (required fields) or inside an
    # Optional-wrapped Annotated (optional fields) — handle both placements.
    base, inline_meta = _split_annotated(_unwrap_optional(field.annotation))
    datatype, allowed_values = _datatype_and_values(base)
    return datatype, allowed_values, _max_length(tuple(field.metadata) + inline_meta)


def reflect_field_shape(model: type[BaseModel], name: str) -> tuple[FieldDatatype, int | None, int | None]:
    """The (datatype, precision, scale) of one canonical field — the translator's
    internal cast-shape source (Slice 16c).

    Reuses the same Annotated peelers + datatype dispatch the catalog builder uses,
    but additionally reads ``max_digits``/``decimal_places`` (precision/scale) off the
    Pydantic Field metadata so the translator can build a decimal ``CastSpec`` per
    dest_key. INTERNAL to translation: precision/scale are deliberately NOT surfaced
    on the catalog wire object (``TemplateMappingField``) — the picker sees no new
    fields. Pure model reflection (no DB round-trip)."""
    field = model.model_fields[name]
    base, inline_meta = _split_annotated(_unwrap_optional(field.annotation))
    datatype, _ = _datatype_and_values(base)
    metadata = tuple(field.metadata) + inline_meta
    return datatype, _max_digits(metadata), _decimal_places(metadata)


def _entries(
    model: type[BaseModel],
    *,
    labels: dict[str, FieldLabel],
    section_of: typing.Callable[[str], FieldSection],
) -> list[TemplateMappingField]:
    """Derive + merge one model's catalog entries, in model declaration order.

    Structure is derived from the model + provenance (drift-guarded); display,
    description and the within-packet section come from ``labels``. ``sink`` is
    the model's single canonical table; ``constraints`` is null in v1.
    """
    produced = mapping_produced_columns(model)  # runs assert_no_drift (provenance guard)
    # Slice 16i: enrichment-value-guaranteed columns (currency on the hot path) are
    # mappable but NOT mandatory — the lib supplies their value, so the picker must not
    # force the user to map them. The column stays in `produced` (a present catalog
    # entry); only its `mandatory` flag drops.
    mandatory = mandatory_mapping_produced(model, enrichment_guaranteed_for(model))
    derived_keys = _assert_no_drift(model, produced, set(labels))
    sink = SINK_BY_MODEL[model]

    entries: list[TemplateMappingField] = []
    for name in derived_keys:
        datatype, allowed_values, max_length = _structural(model, name)
        label = labels[name]
        entries.append(
            TemplateMappingField(
                key=name,
                display_name=label.display_name,
                section=section_of(name),
                mandatory=name in mandatory,
                constraints=None,
                datatype=datatype,
                description=label.description,
                allowed_values=allowed_values,
                max_length=max_length,
                sink=sink,
            )
        )
    return entries


def _event_entries(model: type[BaseModel], wire_section: FieldSection) -> list[TemplateMappingField]:
    """An event type's field set: section is the routed model's single wire label."""
    return _entries(model, labels=LABELS[wire_section], section_of=lambda _name: wire_section)


def _snapshot_entries() -> list[TemplateMappingField]:
    """The catalogue field set: per-field domain section (the __ignore__ sentinel
    is appended uniformly across all types in build_field_catalogs)."""
    return _entries(
        StoreSkuCurrentPosition,
        labels=dict(SNAPSHOT_LABELS),
        section_of=lambda name: typing.cast(FieldSection, SNAPSHOT_LABELS[name].section),
    )


def build_field_catalogs() -> dict[str, list[TemplateMappingField]]:
    """The per-``template_type`` catalogs, each in stable order (section, declaration).

    Keyed by the ``dis_validation`` vocabulary; the type endpoint advertises the
    keys, and the type-aware ``GET /template-mapping-fields`` serves one list.

    The ``__ignore__`` sentinel is appended ONCE here to every type (the single
    inclusion point) so the mistake-proofing target exists on sales, change, and
    snapshot alike.
    """
    return {
        SALES: _event_entries(StoreSkuSaleEvent, "sale_event") + [_IGNORE_FIELD],
        INVENTORY_CHANGE: _event_entries(StoreSkuChangeEvent, "change_event") + [_IGNORE_FIELD],
        SNAPSHOT: _snapshot_entries() + [_IGNORE_FIELD],
    }


def build_field_catalog() -> list[TemplateMappingField]:
    """The flat EVENT field universe (sale + change) — the mapping-suggestion input.

    The per-column suggester (``suggest/``) matches a source column to a canonical
    field across a flat candidate list; it predates the type axis and is out of
    Slice 14d's scope, so it keeps the event field set it always had. Type-aware
    suggestions (including the catalogue field set) are a later slice. The
    ``__ignore__`` sentinel is deliberately NOT included here — it is a mapping-UI
    target, never a suggestion candidate."""
    return _event_entries(StoreSkuSaleEvent, "sale_event") + _event_entries(
        StoreSkuChangeEvent, "change_event"
    )
