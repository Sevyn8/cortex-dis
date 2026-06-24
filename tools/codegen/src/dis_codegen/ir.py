"""The canonical-schema IR (rev-2) and its loader.

Mirrors the field shape in atlas-canonical-schema-IR-spec.md section 3 and the
per-template-type system profile in section 4. Only what A1 generation needs is
modelled with intent; the authored catalog metadata (``display_name`` /
``description`` / ``section``, spec section 3, active from A4) is parsed and
carried but unused by the A1 generator.

Pure parsing: no I/O beyond reading the IR document handed in by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class ProducedBy(StrEnum):
    """Runtime production class (spec section 2). Mirrors ``dis_validation`` verbatim."""

    CONSUMER_INJECTED = "consumer_injected"
    DB_GENERATED = "db_generated"
    COMPUTE_OWNED = "compute_owned"
    ENRICHMENT_PRODUCED = "enrichment_produced"
    MAPPING_PRODUCED = "mapping_produced"


@dataclass(frozen=True)
class TypeIR:
    """A constrained scalar (a ``types`` entry, or an inline field type)."""

    base: str
    precision: int | None = None
    scale: int | None = None
    max_length: int | None = None
    min_length: int | None = None


@dataclass(frozen=True)
class FieldIR:
    """One canonical column (spec section 3, per field)."""

    name: str
    produced_by: ProducedBy
    type_ref: str
    nullable: bool = False
    default: str | None = None
    mandatory: bool = False
    max_length: int | None = None
    inline_type: TypeIR | None = None
    enum_ref: str | None = None
    origin: str | None = None
    # Authored catalog metadata (A4); carried, not used by the A1 generator.
    display_name: str | None = None
    description: str | None = None
    section: str | None = None


@dataclass(frozen=True)
class TableIR:
    """One canonical table (spec section 3, per table)."""

    key: str
    template_type: str
    semantics: str
    sink: str
    natural_key: tuple[str, ...]
    fields: tuple[FieldIR, ...]


@dataclass(frozen=True)
class SchemaIR:
    """One ``(vertical, schema_version)`` IR document."""

    vertical: str
    schema_version: int
    status: str
    system_profile: str
    types: dict[str, TypeIR]
    enums: dict[str, tuple[str, ...]]
    tables: tuple[TableIR, ...] = field(default_factory=tuple)


def _type_ir_from_dict(raw: dict[str, Any]) -> TypeIR:
    return TypeIR(
        base=str(raw["base"]),
        precision=raw.get("precision"),
        scale=raw.get("scale"),
        max_length=raw.get("max_length"),
        min_length=raw.get("min_length"),
    )


def _field_from_dict(raw: dict[str, Any]) -> FieldIR:
    raw_type = raw["type"]
    inline_type: TypeIR | None = None
    if isinstance(raw_type, dict):
        inline_type = _type_ir_from_dict(raw_type)
        type_ref = inline_type.base
    else:
        type_ref = str(raw_type)
    return FieldIR(
        name=str(raw["name"]),
        produced_by=ProducedBy(raw["produced_by"]),
        type_ref=type_ref,
        nullable=bool(raw.get("nullable", False)),
        default=raw.get("default"),
        mandatory=bool(raw.get("mandatory", False)),
        max_length=raw.get("max_length"),
        inline_type=inline_type,
        enum_ref=raw.get("enum_ref"),
        origin=raw.get("origin"),
        display_name=raw.get("display_name"),
        description=raw.get("description"),
        section=raw.get("section"),
    )


def _table_from_dict(raw: dict[str, Any]) -> TableIR:
    return TableIR(
        key=str(raw["key"]),
        template_type=str(raw["template_type"]),
        semantics=str(raw["semantics"]),
        sink=str(raw["sink"]),
        natural_key=tuple(str(k) for k in raw.get("natural_key", [])),
        fields=tuple(_field_from_dict(f) for f in raw["fields"]),
    )


def parse_ir(document: dict[str, Any]) -> SchemaIR:
    """Build a ``SchemaIR`` from an already-parsed IR mapping."""
    types = {name: _type_ir_from_dict(spec) for name, spec in document.get("types", {}).items()}
    enums = {name: tuple(str(v) for v in values) for name, values in document.get("enums", {}).items()}
    return SchemaIR(
        vertical=str(document["vertical"]),
        schema_version=int(document["schema_version"]),
        status=str(document["status"]),
        system_profile=str(document["system_profile"]),
        types=types,
        enums=enums,
        tables=tuple(_table_from_dict(t) for t in document.get("tables", [])),
    )


def load_ir(path: Path) -> SchemaIR:
    """Load and parse an IR document from a YAML file."""
    document = yaml.safe_load(path.read_text())
    if not isinstance(document, dict):
        raise ValueError(f"IR document at {path} did not parse to a mapping")
    return parse_ir(document)
