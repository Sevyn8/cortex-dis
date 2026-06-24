"""Deterministic generation from one IR table.

Three artifacts, all derived from the IR, never hand-aligned:

- ``render_model`` -> the Pydantic model source (closed, ``extra="forbid"``,
  referencing the shared constrained-type aliases and enums).
- ``render_provenance`` -> the ``dis_validation`` provenance partition source
  (the five-way classification, bucketed from each field's ``produced_by``).
- ``render_ddl`` -> the IR-EXPRESSIBLE DDL SUBSET as an in-memory ``DdlSubset``
  (table + columns + the natural-key COALESCE-sentinel unique index + the
  ``<> ''`` sentinel CHECKs). ``DdlSubset.to_partial_sql`` renders a banner-marked
  ``.sql.partial`` fragment. A1 emits NO runnable SQL: the full reconciled DDL is
  an A2 artifact (generated-DDL-vs-live-schema, requires a DB). See README.md.

The Optional rule (spec section 5): a model field is ``X | None = None`` iff
``nullable`` OR ``default is not None`` OR ``produced_by == db_generated``. The
mappable set is exactly ``produced_by == mapping_produced``; the other four classes
are generated into the model and DDL but excluded from the catalog partition.
"""

from __future__ import annotations

from dataclasses import dataclass

from dis_codegen.ir import FieldIR, ProducedBy, SchemaIR, TableIR, TypeIR

# (precision, scale) -> the dis-canonical shared Decimal alias.
_NUMERIC_ALIASES: dict[tuple[int, int], str] = {
    (12, 4): "Numeric12_4",
    (14, 3): "Numeric14_3",
    (14, 4): "Numeric14_4",
    (10, 4): "Numeric10_4",
    (8, 3): "Numeric8_3",
    (5, 2): "Numeric5_2",
    (3, 2): "Numeric3_2",
}
# varchar(n) -> the shared string alias.
_STR_ALIASES: dict[int, str] = {32: "Str32", 64: "Str64", 128: "Str128", 256: "Str256"}
# enum_ref -> the shared enum class.
_ENUM_ALIASES: dict[str, str] = {"tax_treatment": "TaxTreatment", "expiry_source": "ExpirySource"}

# base type -> the SQL type rendered in the partial DDL subset (collation deliberately
# omitted; it is not in the IR and is an A2 reconciliation item, see README.md).
_SQL_INT = {"int": "INTEGER", "smallint": "SMALLINT", "bigint": "BIGINT"}


@dataclass(frozen=True)
class _Resolved:
    """A field's resolved type: the Python annotation, the shared imports it needs,
    the stdlib imports it needs, and the SQL type for the DDL subset."""

    annotation: str
    shared_imports: frozenset[str]
    stdlib_imports: frozenset[str]
    sql_type: str


def optional_rule(f: FieldIR) -> bool:
    """Spec section 5: Optional iff nullable OR has a default OR db_generated."""
    return f.nullable or f.default is not None or f.produced_by is ProducedBy.DB_GENERATED


def _effective_type(f: FieldIR, schema: SchemaIR) -> tuple[str, TypeIR | None, int | None]:
    """Resolve a field to ``(base, constrained_type_or_none, field_max_length)``."""
    if f.type_ref in schema.types:
        t = schema.types[f.type_ref]
        return t.base, t, f.max_length
    if f.inline_type is not None:
        return f.inline_type.base, f.inline_type, f.max_length
    return f.type_ref, None, f.max_length


def _resolve(f: FieldIR, schema: SchemaIR) -> _Resolved:
    """Resolve a field to its generated annotation, imports, and SQL type."""
    if f.enum_ref is not None:
        alias = _ENUM_ALIASES.get(f.enum_ref)
        if alias is None:
            raise ValueError(f"no shared enum alias for enum_ref {f.enum_ref!r} (field {f.name})")
        sql = f"canonical.{f.enum_ref}_enum"
        return _Resolved(alias, frozenset({alias}), frozenset(), sql)

    base, constrained, field_max = _effective_type(f, schema)

    if base == "decimal":
        if constrained is None or constrained.precision is None or constrained.scale is None:
            raise ValueError(f"decimal field {f.name} lacks precision/scale")
        key = (constrained.precision, constrained.scale)
        alias = _NUMERIC_ALIASES.get(key)
        if alias is None:
            raise ValueError(f"no shared Decimal alias for numeric{key} (field {f.name})")
        return _Resolved(alias, frozenset({alias}), frozenset(), f"NUMERIC({key[0]}, {key[1]})")

    if base == "str":
        min_len = constrained.min_length if constrained is not None else None
        max_len = (constrained.max_length if constrained is not None else None) or field_max
        if min_len == 3 and max_len == 3:
            return _Resolved("CurrencyCode", frozenset({"CurrencyCode"}), frozenset(), "CHAR(3)")
        if max_len in _STR_ALIASES:
            alias = _STR_ALIASES[max_len]
            return _Resolved(alias, frozenset({alias}), frozenset(), f"VARCHAR({max_len})")
        raise ValueError(f"no shared string alias for str field {f.name} (max_length={max_len})")

    if base == "uuid":
        return _Resolved("UUID", frozenset(), frozenset({"uuid.UUID"}), "UUID")
    if base in _SQL_INT:
        return _Resolved("int", frozenset(), frozenset(), _SQL_INT[base])
    if base == "bool":
        return _Resolved("bool", frozenset(), frozenset(), "BOOLEAN")
    if base == "date":
        return _Resolved("date", frozenset(), frozenset({"datetime.date"}), "DATE")
    if base in ("timestamptz", "datetime"):
        return _Resolved("datetime", frozenset(), frozenset({"datetime.datetime"}), "TIMESTAMPTZ")
    if base == "jsonb":
        return _Resolved("dict[str, Any]", frozenset(), frozenset({"typing.Any"}), "JSONB")

    raise ValueError(f"unknown base type {base!r} for field {f.name}")


def _class_name(table_key: str) -> str:
    """``store_sku_current_position`` -> ``StoreSkuCurrentPosition``."""
    return "".join(part.capitalize() for part in table_key.split("_"))


def _stdlib_import_lines(symbols: frozenset[str]) -> list[str]:
    """Render grouped stdlib import lines from dotted symbol names."""
    by_module: dict[str, set[str]] = {}
    for dotted in symbols:
        module, name = dotted.rsplit(".", 1)
        by_module.setdefault(module, set()).add(name)
    lines: list[str] = []
    for module in sorted(by_module):
        names = ", ".join(sorted(by_module[module]))
        lines.append(f"from {module} import {names}")
    return lines


def render_model(table: TableIR, schema: SchemaIR) -> str:
    """The Pydantic model source for one IR table."""
    shared: set[str] = {"CanonicalModel"}
    stdlib: set[str] = set()
    body: list[str] = []
    for f in table.fields:
        resolved = _resolve(f, schema)
        shared |= resolved.shared_imports
        stdlib |= resolved.stdlib_imports
        if optional_rule(f):
            body.append(f"    {f.name}: {resolved.annotation} | None = None")
        else:
            body.append(f"    {f.name}: {resolved.annotation}")

    lines = ['"""GENERATED from the Atlas IR. Do not hand-edit; edit the IR and regenerate."""', ""]
    lines.append("from __future__ import annotations")
    lines.append("")
    stdlib_lines = _stdlib_import_lines(frozenset(stdlib))
    if stdlib_lines:
        lines.extend(stdlib_lines)
        lines.append("")
    shared_names = ",\n    ".join(sorted(shared))
    lines.append(f"from dis_canonical.shared import (\n    {shared_names},\n)")
    lines.append("")
    lines.append("")
    lines.append(f"class {_class_name(table.key)}(CanonicalModel):")
    lines.extend(body)
    lines.append("")
    return "\n".join(lines)


def _partition(table: TableIR) -> dict[ProducedBy, list[str]]:
    buckets: dict[ProducedBy, list[str]] = {pb: [] for pb in ProducedBy}
    for f in table.fields:
        buckets[f.produced_by].append(f.name)
    return buckets


def render_provenance(table: TableIR) -> str:
    """The ``dis_validation`` provenance partition source for one IR table."""
    buckets = _partition(table)

    def _frozenset_literal(names: list[str]) -> str:
        if not names:
            return "frozenset()"
        inner = ",\n        ".join(repr(n) for n in names)
        return f"frozenset(\n        {{\n        {inner},\n        }}\n    )"

    lines = ['"""GENERATED from the Atlas IR. Do not hand-edit; edit the IR and regenerate."""', ""]
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from dis_validation import ColumnProvenance")
    lines.append("")
    lines.append("")
    lines.append("PROVENANCE = ColumnProvenance(")
    lines.append(f"    consumer_injected={_frozenset_literal(buckets[ProducedBy.CONSUMER_INJECTED])},")
    lines.append(f"    db_generated={_frozenset_literal(buckets[ProducedBy.DB_GENERATED])},")
    lines.append(f"    compute_owned={_frozenset_literal(buckets[ProducedBy.COMPUTE_OWNED])},")
    lines.append(f"    mapping_produced={_frozenset_literal(buckets[ProducedBy.MAPPING_PRODUCED])},")
    lines.append(f"    enrichment_produced={_frozenset_literal(buckets[ProducedBy.ENRICHMENT_PRODUCED])},")
    lines.append(")")
    lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class DdlSubset:
    """The IR-expressible DDL subset for one table. NOT a runnable migration."""

    table: str
    sink: str
    columns: tuple[tuple[str, str, bool, str | None], ...]  # (name, sql_type, nullable, default)
    natural_key_unique_index: str
    sentinel_checks: tuple[str, ...]

    def to_partial_sql(self) -> str:
        """A banner-marked ``.sql.partial`` fragment. Deliberately incomplete and
        non-runnable: no enum types, no FKs, no value-range/cross-field CHECKs, no
        RLS, no trigger, no secondary indexes, no comments, no collation."""
        banner = [
            "-- ATLAS A1 ARTIFACT: IR-EXPRESSIBLE SUBSET ONLY. NOT A MIGRATION. NOT RUNNABLE.",
            "-- Subset = columns + natural-key COALESCE-sentinel unique index + sentinel CHECKs.",
            "-- The full, reconciled, runnable DDL is an A2 artifact (generated-DDL-vs-live-schema,",
            "-- requires a DB). See tools/codegen/README.md for the deferred DDL-fidelity items.",
            "",
        ]
        cols = []
        for name, sql_type, nullable, default in self.columns:
            null = "NULL" if nullable else "NOT NULL"
            default_sql = f" DEFAULT {default}" if default else ""
            cols.append(f"    {name} {sql_type} {null}{default_sql}")
        checks = [f"    CHECK ({c})" for c in self.sentinel_checks]
        table_block = (
            f"-- columns for {self.sink} (subset)\n"
            + "\n".join(cols)
            + ("\n" + "\n".join(checks) if checks else "")
        )
        return "\n".join(banner) + table_block + "\n\n-- natural key\n" + self.natural_key_unique_index + "\n"


def render_ddl(table: TableIR, schema: SchemaIR) -> DdlSubset:
    """The IR-expressible DDL subset for one IR table (in-memory)."""
    by_name = {f.name: f for f in table.fields}
    columns: list[tuple[str, str, bool, str | None]] = []
    for f in table.fields:
        resolved = _resolve(f, schema)
        columns.append((f.name, resolved.sql_type, f.nullable, f.default))

    # Natural-key COALESCE-sentinel unique index (spec section 5): tenant_id is the
    # RLS scoping prefix, then each natural_key member bare (NOT NULL) or COALESCE-
    # wrapped (nullable), with a ``<> ''`` sentinel CHECK on each nullable member.
    index_cols = ["tenant_id"]
    sentinel_checks: list[str] = []
    for member in table.natural_key:
        field_def = by_name.get(member)
        if field_def is not None and field_def.nullable:
            index_cols.append(f"COALESCE({member}, '')")
            sentinel_checks.append(f"{member} <> ''")
        else:
            index_cols.append(member)
    index = f"CREATE UNIQUE INDEX uq_{table.key}_natural_key ON {table.sink} ({', '.join(index_cols)});"

    return DdlSubset(
        table=table.key,
        sink=table.sink,
        columns=tuple(columns),
        natural_key_unique_index=index,
        sentinel_checks=tuple(sentinel_checks),
    )
