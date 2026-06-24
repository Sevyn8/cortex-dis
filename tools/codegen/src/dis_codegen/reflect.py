"""Constrained-type identity for the prover.

``type_signature`` reduces a Pydantic ``FieldInfo`` to a comparable tuple that
captures the *constrained* shape, not just the base Python type: a Decimal field's
precision and scale, a string field's min/max length, an enum's concrete class, a
Literal's value set. Two fields compare equal iff they are the SAME constrained
alias: ``Numeric12_4`` equals ``Numeric12_4`` but not ``Numeric14_3`` and not a
bare ``Decimal``; ``Str128`` not ``Str64``; ``CurrencyCode`` (min=max=3) not
``Str32``.

The Annotated/metadata peelers mirror those in
``services/dis-ui-server/.../catalog/field_catalog.py`` (``_unwrap_optional``,
``_split_annotated``, the nested-metadata int search), reimplemented here so the
generator stays self-contained and does not import a service. Pure reflection.
"""

from __future__ import annotations

import enum
import types as _types
import typing
from typing import Any

from pydantic.fields import FieldInfo


def _unwrap_optional(annotation: Any) -> Any:
    """Drop ``None`` from ``X | None`` / ``Optional[X]``; return the remaining type."""
    if typing.get_origin(annotation) in (typing.Union, _types.UnionType):
        members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _split_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """Split ``Annotated[base, *meta]`` into ``(base, meta)``; plain types pass through."""
    if typing.get_args(annotation) and hasattr(annotation, "__metadata__"):
        args = typing.get_args(annotation)
        return args[0], tuple(args[1:])
    return annotation, ()


def _meta_int(metadata: tuple[Any, ...], attr: str) -> int | None:
    """Find an int constraint ``attr`` across metadata, descending one level into the
    ``FieldInfo`` wrapper that an Optional field nests its constraint under."""
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


def _base_marker(base: Any) -> tuple[str, Any]:
    """A comparable marker for the base type: enum class identity, Literal value set,
    dict type args, or the plain Python type."""
    if typing.get_origin(base) is typing.Literal:
        return ("literal", tuple(str(value) for value in typing.get_args(base)))
    if isinstance(base, type) and issubclass(base, enum.Enum):
        return ("enum", base)
    if typing.get_origin(base) is dict or base is dict:
        return ("dict", typing.get_args(base))
    if isinstance(base, type):
        return ("type", base)
    return ("other", base)


def type_signature(field: FieldInfo) -> tuple[Any, ...]:
    """The constrained-type signature of one model field.

    ``(base_marker, min_length, max_length, max_digits, decimal_places)``. Equal
    signatures mean the same constrained alias, not merely the same base type.
    """
    base, inline_meta = _split_annotated(_unwrap_optional(field.annotation))
    metadata = tuple(field.metadata) + inline_meta
    return (
        _base_marker(base),
        _meta_int(metadata, "min_length"),
        _meta_int(metadata, "max_length"),
        _meta_int(metadata, "max_digits"),
        _meta_int(metadata, "decimal_places"),
    )
