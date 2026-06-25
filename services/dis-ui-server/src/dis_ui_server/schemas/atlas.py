"""Wire shapes for the Atlas console endpoints (A4 PR1).

The draft IR is rendered field-by-field for the editor grid, with a derived
``curated_bearing`` flag per field so the UI can show what still needs ratification.
The PATCH request edits mapping-produced fields and ratifies (flips ``origin`` to
``human``); system fields are read-only (IR spec section 4).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from dis_codegen import SchemaIR, is_curated_bearing
from dis_codegen.ir import FieldIR, TableIR


class AtlasFieldModel(BaseModel):
    """One canonical field as the editor grid sees it."""

    name: str
    produced_by: str
    type_ref: str
    nullable: bool
    default: str | None = None
    mandatory: bool
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    enum_ref: str | None = None
    enum_candidate: list[str] = Field(default_factory=list)
    pii: str | None = None
    origin: str | None
    display_name: str | None = None
    description: str | None = None
    provenance: dict[str, Any] | None = None
    # Derived for the UI (never stored): whether this field needs human ratification.
    curated_bearing: bool


class AtlasTableModel(BaseModel):
    key: str
    template_type: str
    semantics: str
    sink: str
    natural_key: list[str]
    fields: list[AtlasFieldModel]


class AtlasDraftResponse(BaseModel):
    """A draft (or frozen) IR over the wire."""

    draft_id: str
    vertical: str
    status: str
    schema_version: int
    system_profile: str
    table: AtlasTableModel


class FieldEdit(BaseModel):
    """One field edit. Only provided attributes change; ``origin`` flips on ratify.

    PR1 ratifies at FIELD granularity (one ``origin`` per field); a provided value
    sets the attribute (clearing ``pii`` back to null is out of PR1 scope)."""

    name: str
    nullable: bool | None = None
    mandatory: bool | None = None
    pii: str | None = None
    enum_candidate: list[str] | None = None
    origin: Literal["inferred", "human"] | None = None


class DraftPatch(BaseModel):
    """Edits applied to a draft: the table-level natural key plus per-field edits."""

    natural_key: list[str] | None = None
    fields: list[FieldEdit] = Field(default_factory=list)


class PublishReceipt(BaseModel):
    """The post-publish confirmation (the publish-receipt surface)."""

    draft_id: str
    vertical: str
    status: str
    schema_version: int
    audit_emitted: bool


def _field_to_wire(field: FieldIR, table: TableIR) -> AtlasFieldModel:
    provenance = None
    if field.provenance is not None:
        provenance = {
            "introduced_in": field.provenance.introduced_in,
            "source_headers": list(field.provenance.source_headers),
            "present_in_files": field.provenance.present_in_files,
            "total_files": field.provenance.total_files,
            "rows_profiled": field.provenance.rows_profiled,
        }
    precision = field.inline_type.precision if field.inline_type is not None else None
    scale = field.inline_type.scale if field.inline_type is not None else None
    return AtlasFieldModel(
        name=field.name,
        produced_by=field.produced_by.value,
        type_ref=field.type_ref,
        nullable=field.nullable,
        default=field.default,
        mandatory=field.mandatory,
        max_length=field.max_length,
        precision=precision,
        scale=scale,
        enum_ref=field.enum_ref,
        enum_candidate=list(field.enum_candidate),
        pii=field.pii,
        origin=field.origin,
        display_name=field.display_name,
        description=field.description,
        provenance=provenance,
        curated_bearing=is_curated_bearing(field, table),
    )


def draft_to_wire(draft_id: str, schema: SchemaIR) -> AtlasDraftResponse:
    """Render a draft (or frozen) SchemaIR for the console."""
    table = schema.tables[0]
    return AtlasDraftResponse(
        draft_id=draft_id,
        vertical=schema.vertical,
        status=schema.status,
        schema_version=schema.schema_version,
        system_profile=schema.system_profile,
        table=AtlasTableModel(
            key=table.key,
            template_type=table.template_type,
            semantics=table.semantics,
            sink=table.sink,
            natural_key=list(table.natural_key),
            fields=[_field_to_wire(f, table) for f in table.fields],
        ),
    )
