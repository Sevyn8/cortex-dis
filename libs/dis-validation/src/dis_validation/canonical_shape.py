"""Canonical-shape (post-mapping) suite: judges a mapped contribution (slice-05).

The suite scopes to ONE named ``dis-canonical`` model restricted to the
source-owned, mapping-produced columns (D8): field set, dtype, nullability,
max-length/digits, and enum vocab are DERIVED from the model's ``model_fields``
(the single description of canonical shape — OQ7); business invariants (range
bounds, identifier patterns, cross-field consistency) are AUTHORED on the
definition. ``strict=True`` so an off-universe column fails loud.

What is deliberately NOT here: existence checks against ``identity_mirror``
(a DB read this pure lib cannot do — the consumer's at write time, Slice 10) and
any check on the consumer-injected columns (identity, ``trace_id``,
``mapping_version_id``) — the contribution never carries them.
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pandera.polars as pa
import polars as pl
from pandera import Check
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from dis_core.errors import SuiteDefinitionError, SuiteDriftError
from dis_validation.provenance import enrichment_produced_columns, mapping_produced_columns


@dataclass(frozen=True)
class CanonicalShapeSuiteDef:
    """One source's canonical-shape suite definition for one target model.

    A plain dataclass (not Pydantic): authored invariants are pandera ``Check``
    objects, which are validated by materialization, not by field coercion.
    """

    target_model: type[BaseModel]
    owned_columns: tuple[str, ...]
    # Authored business invariants: per-column and frame-level (cross-field).
    column_checks: dict[str, list[Check]] = field(default_factory=dict)
    frame_checks: list[Check] = field(default_factory=list)


@dataclass(frozen=True)
class _ResolvedAnnotation:
    base: Any
    metadata: tuple[Any, ...]
    nullable: bool


def _resolve_annotation(annotation: Any) -> _ResolvedAnnotation:
    """Unwrap ``Optional[Annotated[base, meta...]]`` into (base, metadata, nullable)."""
    nullable = False
    current = annotation
    origin = typing.get_origin(current)
    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(current)
        non_none = [a for a in args if a is not type(None)]
        nullable = len(non_none) < len(args)
        if len(non_none) != 1:
            raise SuiteDefinitionError(f"unsupported union annotation {annotation!r}")
        current = non_none[0]
    metadata: tuple[Any, ...] = ()
    if typing.get_origin(current) is not None and hasattr(current, "__metadata__"):
        metadata = _flatten_metadata(current.__metadata__)
        current = typing.get_args(current)[0]
    return _ResolvedAnnotation(base=current, metadata=metadata, nullable=nullable)


def _flatten_metadata(raw: tuple[Any, ...]) -> tuple[Any, ...]:
    """Expand any pydantic ``FieldInfo`` metadata item into its decomposed constraints.

    A type alias built with ``Field(...)`` — e.g. ``Numeric14_3 =
    Annotated[Decimal, Field(max_digits=14, decimal_places=3)]`` — surfaces as a
    bare ``FieldInfo`` in ``Annotated.__metadata__`` when the field is OPTIONAL
    (``X | None``): pydantic only decomposes the constraints onto the model field's
    own ``.metadata`` for REQUIRED fields. The constraint objects (``max_digits`` /
    ``max_length`` / ...) live in ``FieldInfo.metadata``, so flatten them here to
    match the required-field path; without this an owned optional Decimal column
    (e.g. the catalogue's ``stock_qty``) cannot derive its numeric dtype."""
    flat: list[Any] = []
    for item in raw:
        if isinstance(item, FieldInfo):
            flat.extend(item.metadata)
        else:
            flat.append(item)
    return tuple(flat)


def _column_from_field(
    model: type[BaseModel], name: str, info: FieldInfo, authored: list[Check]
) -> pa.Column:
    """Derive one pandera Column from a pydantic field + authored checks (OQ7)."""
    resolved = _resolve_annotation(info.annotation)
    metadata = tuple(info.metadata) + resolved.metadata
    nullable = resolved.nullable or not info.is_required()
    base = resolved.base
    checks = list(authored)
    dtype: pl.DataType | None

    if typing.get_origin(base) is typing.Literal:
        # CHECK-constrained varchar vocab (e.g. event_subtype {SALE,RETURN,VOID}).
        dtype = pl.String()
        checks.append(Check.isin(list(typing.get_args(base))))
    elif isinstance(base, type) and issubclass(base, StrEnum):
        # pg enum (e.g. tax_treatment_enum) — contributions carry the string value.
        dtype = pl.String()
        checks.append(Check.isin([member.value for member in base]))
    elif base is str:
        dtype = pl.String()
        max_length = next(
            (m.max_length for m in metadata if getattr(m, "max_length", None) is not None),
            None,
        )
        if max_length is not None:
            checks.append(Check.str_length(max_value=max_length))
    elif base is Decimal:
        digits = next((m for m in metadata if getattr(m, "max_digits", None) is not None), None)
        if digits is None:
            raise SuiteDefinitionError(
                f"{model.__name__}.{name}: Decimal field carries no max_digits/decimal_places "
                "metadata; cannot derive a numeric dtype",
                model=model.__name__,
                column=name,
            )
        dtype = pl.Decimal(precision=digits.max_digits, scale=digits.decimal_places)
    elif base is int:
        dtype = pl.Int64()
    elif base is bool:
        dtype = pl.Boolean()
    elif base is datetime:
        dtype = pl.Datetime(time_unit="us", time_zone="UTC")
    elif base is date:
        dtype = pl.Date()
    elif base is Any or typing.get_origin(base) is dict:
        # jsonb payloads (value_before/value_after/change_context): shape is
        # polymorphic by design — presence/nullability only, no dtype check.
        dtype = None
    else:
        raise SuiteDefinitionError(
            f"{model.__name__}.{name}: unsupported annotation {info.annotation!r} for suite derivation",
            model=model.__name__,
            column=name,
        )

    return pa.Column(
        dtype,
        checks=checks,
        nullable=nullable,
        required=True,
        coerce=False,  # validation judges the contribution; it never fixes it
        name=name,
    )


def materialize_canonical_shape(definition: CanonicalShapeSuiteDef) -> pa.DataFrameSchema:
    """Turn a definition into a runnable Pandera schema (pure; no DB, no config read).

    Runs the drift guard first: the owned set must be a subset of the model's
    source-owned universe — mapping-produced ∪ enrichment-produced (slice-5b,
    D94/D95) — and STILL rejects consumer-injected / DB-generated / compute-owned
    columns (errors, never skips — criterion 6).
    """
    model = definition.target_model
    # Source-owned for the canonical-shape gate = mapping-produced + enrichment-produced.
    # Enrichment writes canonical values that pass the SAME gate (D94); widening to admit
    # them must NOT admit consumer-injected/DB-generated/compute-owned columns.
    produced = mapping_produced_columns(model) | enrichment_produced_columns(model)

    owned = tuple(dict.fromkeys(definition.owned_columns))
    if not owned:
        raise SuiteDefinitionError(
            f"{model.__name__}: owned_columns is empty — a source owns at least one column (D8)",
            model=model.__name__,
        )
    off_universe = [column for column in owned if column not in produced]
    if off_universe:
        raise SuiteDriftError(
            f"{model.__name__}: owned column(s) {off_universe} are not in the model's "
            "source-owned set (mapping-produced ∪ enrichment-produced) — consumer-injected/"
            "DB-generated/compute-owned columns cannot be source-owned",
            model=model.__name__,
            column=off_universe[0],
        )
    unknown_check_columns = [c for c in definition.column_checks if c not in owned]
    if unknown_check_columns:
        raise SuiteDefinitionError(
            f"{model.__name__}: column_checks name non-owned column(s) {unknown_check_columns}",
            model=model.__name__,
            column=unknown_check_columns[0],
        )

    columns = {
        name: _column_from_field(
            model, name, model.model_fields[name], definition.column_checks.get(name, [])
        )
        for name in owned
    }
    return pa.DataFrameSchema(
        columns,
        checks=list(definition.frame_checks),
        strict=True,  # an off-universe column in the contribution fails loud
        name=f"canonical_shape:{model.__name__}",
    )


def suite_column_set(schema: pa.DataFrameSchema) -> frozenset[str]:
    """The materialized suite's column set (used by the drift-guard tests)."""
    return frozenset(schema.columns)
