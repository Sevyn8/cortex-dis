"""Slice 16c: translate the create CONTRACT into an engine ``mapping_rules`` document.

The ``POST /mapping-templates`` request (Slice 16a) carries semantic intent per column
(``src_key`` -> ``dest_key`` + source-format declarations), NOT engine ops. This module
turns that into a ``dis_mapping.SourceMapping`` dict (rename / normalize / cast / derive):

- **rename** — ``{src_key: dest_key}`` for every column whose ``dest_key`` is real;
  ``__ignore__`` columns are dropped entirely (no rename / normalize / cast).
- **normalize** — derived from the declarations: a date/datetime token -> ``parse_date`` or
  ``parse_datetime`` (chosen by the TARGET column's canonical datatype, never the request);
  a decimal/percentage column -> ``parse_decimal`` / ``parse_percent`` with the declared
  separators (thousands ABSENT -> an explicit ``null`` arg).
- **cast** — derived from the target's canonical datatype; decimal carries the precision and
  scale reflected from the dis-canonical model (internal, never the request).
- **derive** — always empty: the contract does not express derive (Slice 16c scope).

The produced dict is handed to ``validate_mapping_rules_for_type`` (the semantic gate) BEFORE
any write; an unknown date token (outside the locked five) or an unmappable datatype is a
clean ``MappingConfigError`` (-> 400) here, so nothing invalid can reach the gate or the DB.

The accepted DATE-format token set is exactly five, in lockstep with the frontend picker
(``services/dis-ui/src/components/locale-rules.ts``); a sixth is added backend-side before the
picker offers it. The contract carries the friendly token (``DD-MM-YYYY``); the engine takes a
strptime code (``%d-%m-%Y``), so this module owns the conversion.
"""

from __future__ import annotations

from typing import Any

from dis_core.errors import MappingConfigError
from dis_ui_server.catalog.field_catalog import reflect_field_shape
from dis_ui_server.schemas.mapping_fields import FieldDatatype
from dis_ui_server.schemas.mapping_templates import MappingColumn, MappingTemplateCreate
from dis_validation import model_for_template_type

_IGNORE = "__ignore__"

# Fixed UTC for every datetime target: the contract carries no zone, the canonical
# datetime columns are UTC, and parse_datetime forbids a null timezone unless the format
# carries a %z offset (none of the five date tokens do). "UTC" is the only consistent value.
_DATETIME_TIMEZONE = "UTC"

# The locked five (slice decision): friendly token -> strptime code. A token outside this
# exact set is a translation error (-> 400). Held in lockstep with the frontend picker.
_DATE_TOKEN_TO_STRPTIME: dict[str, str] = {
    "DD-MM-YYYY": "%d-%m-%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD-MM-YY": "%d-%m-%y",
}

# Canonical datatype (field-catalog vocabulary) -> engine cast ``type``. ``choice`` (enum /
# Literal columns, e.g. event_subtype) casts to string; ``json`` has no mapping-produced
# target and is intentionally absent (an unmappable datatype is a loud 400, never a default).
_CAST_TYPE_BY_DATATYPE: dict[FieldDatatype, str] = {
    "text": "string",
    "choice": "string",
    "integer": "integer",
    "number": "decimal",
    "date": "date",
    "datetime": "datetime",
    "boolean": "boolean",
}


def date_token_to_strptime(token: str, *, tenant_id: str) -> str:
    """Convert a friendly date token (``DD-MM-YYYY``) to its strptime code (``%d-%m-%Y``).

    A token outside the locked five raises ``MappingConfigError`` (-> 400); the conversion
    table and the picker stay in lockstep."""
    code = _DATE_TOKEN_TO_STRPTIME.get(token)
    if code is None:
        raise MappingConfigError(
            f"unknown date format token {token!r}; the accepted set is "
            f"{sorted(_DATE_TOKEN_TO_STRPTIME)} (the picker and backend stay in lockstep)",
            tenant_id=tenant_id,
        )
    return code


def _normalize_ops(column: MappingColumn, datatype: FieldDatatype, *, tenant_id: str) -> list[dict[str, Any]]:
    """The ordered normalize ops for one column, from its declarations + target datatype.

    A normalize op is emitted ONLY when a declaration calls for it: a string column with no
    declaration gets rename + cast only. The parse op is chosen by the target datatype, never
    the request (a date column never receives a datetime op, and vice versa)."""
    ops: list[dict[str, Any]] = []

    if column.src_datetime_format is not None and datatype in ("date", "datetime"):
        fmt = date_token_to_strptime(column.src_datetime_format, tenant_id=tenant_id)
        if datatype == "date":
            ops.append({"op": "parse_date", "args": {"format": fmt}})
        else:
            ops.append({"op": "parse_datetime", "args": {"format": fmt, "timezone": _DATETIME_TIMEZONE}})

    if datatype == "number":
        # Both separators ride into the op; an absent thousands declaration is an EXPLICIT
        # null (the op requires the key). A missing decimal_separator stays None and the
        # SourceMapping op-arg validator rejects it (-> 400) — never defaulted here.
        if column.src_is_percentage:
            ops.append(
                {
                    "op": "parse_percent",
                    "args": {
                        "decimal_separator": column.src_decimal_separator,
                        "thousands_separator": column.src_thousand_separator,
                    },
                }
            )
        elif column.src_decimal_separator is not None:
            ops.append(
                {
                    "op": "parse_decimal",
                    "args": {
                        "decimal_separator": column.src_decimal_separator,
                        "thousands_separator": column.src_thousand_separator,
                    },
                }
            )

    return ops


def _cast_spec(
    datatype: FieldDatatype, precision: int | None, scale: int | None, dest_key: str, *, tenant_id: str
) -> dict[str, Any]:
    """The cast spec for one target, from its canonical datatype.

    Decimal carries the reflected precision/scale (internal, from the dis-canonical model);
    all other types are type-only. A datatype with no cast target is a loud 400."""
    cast_type = _CAST_TYPE_BY_DATATYPE.get(datatype)
    if cast_type is None:
        raise MappingConfigError(
            f"dest_key {dest_key!r} has canonical datatype {datatype!r}, which has no cast "
            "target (Slice 16c maps text/choice/integer/number/date/datetime/boolean)",
            tenant_id=tenant_id,
        )
    spec: dict[str, Any] = {"type": cast_type}
    if cast_type == "decimal":
        # Pass precision/scale through even when None: the CastSpec validator then refuses
        # the decimal cast (-> 400) rather than letting an unscaled decimal be stored.
        spec["precision"] = precision
        spec["scale"] = scale
    return spec


def translate_columns_to_mapping_rules(body: MappingTemplateCreate, *, tenant_id: str) -> dict[str, Any]:
    """Translate the Slice 16a create contract into a ``mapping_rules`` document (dict).

    Routes by ``template_type`` to the canonical model, then per column builds rename +
    (declaration-driven) normalize + (datatype-driven) cast. ``__ignore__`` columns are
    dropped. An unknown ``dest_key`` gets a rename entry only (no reflection): the semantic
    gate's ``check_target_legality`` rejects it with a clean 400. derive is always empty.

    The result is NOT yet semantically validated — the caller runs
    ``validate_mapping_rules_for_type`` on it before any write."""
    model = model_for_template_type(body.template_type)
    model_fields = model.model_fields

    rename: dict[str, str] = {}
    normalize: dict[str, list[dict[str, Any]]] = {}
    cast: dict[str, dict[str, Any]] = {}

    for column in body.columns:
        dest_key = column.dest_key
        if dest_key == _IGNORE:
            continue
        rename[column.src_key] = dest_key
        if dest_key not in model_fields:
            # Unknown target: rename only. Reflecting would KeyError, and guessing a shape
            # would mask the error — let check_target_legality own the rejection (400).
            continue
        datatype, precision, scale = reflect_field_shape(model, dest_key)
        ops = _normalize_ops(column, datatype, tenant_id=tenant_id)
        if ops:
            normalize[dest_key] = ops
        cast[dest_key] = _cast_spec(datatype, precision, scale, dest_key, tenant_id=tenant_id)

    return {"version": 1, "rename": rename, "normalize": normalize, "cast": cast, "derive": {}}
