"""Atlas A3 draft-IR assembly, validation, and YAML emission.

A3 produces a DRAFT IR (the section-3 shape) from profiled CSVs: inference
PROPOSES the mapping-produced business fields; the fixed section-4 system profile
is INJECTED (never inferred); the curated layer is always flagged for ratify.

This module is the schema authority (it owns the A1 IR dataclasses): it injects
the fixed snapshot system profile, assembles the draft from the (profiler,
proposer) payloads handed over the dict boundary, validates the origin/produced_by
invariants, and emits the YAML handoff artifact. The deterministic profiling and
the LLM proposing live in dis-ui-server; the dict payloads are the in-memory form
of the YAML handoff (no service<->tool Python dependency).
"""

from __future__ import annotations

from typing import Any

import yaml

from dis_codegen.ir import FieldIR, FieldProvenance, ProducedBy, SchemaIR, TableIR, TypeIR

# The fixed section-4 snapshot system profile for retail: the invariant core
# (id, tenant_id, mapping_version_id, trace_id, dis_channel, ingest_metadata,
# last_updated_at), the snapshot addition (last_source_event_at), and the retail
# entity key (store_id). INJECTED, never inferred; stamped origin "human" (locked).
# A non-retail vertical's entity key is a Customer Master dependency (Finding 4),
# out of A3 scope. The layer-(c) anti-drift test asserts these equal the live
# retail system fields exactly (name + produced_by + nullable + default), so this
# hardcoded template cannot silently drift from reality.
SNAPSHOT_SYSTEM_PROFILE: tuple[FieldIR, ...] = (
    FieldIR("id", ProducedBy.DB_GENERATED, "uuid", nullable=False, default="uuidv7()", origin="human"),
    FieldIR("tenant_id", ProducedBy.CONSUMER_INJECTED, "uuid", nullable=False, origin="human"),
    FieldIR("store_id", ProducedBy.CONSUMER_INJECTED, "uuid", nullable=False, origin="human"),
    FieldIR(
        "last_source_event_at", ProducedBy.CONSUMER_INJECTED, "timestamptz", nullable=True, origin="human"
    ),
    FieldIR("mapping_version_id", ProducedBy.CONSUMER_INJECTED, "bigint", nullable=False, origin="human"),
    FieldIR("trace_id", ProducedBy.CONSUMER_INJECTED, "uuid", nullable=False, origin="human"),
    FieldIR(
        "dis_channel", ProducedBy.CONSUMER_INJECTED, "str", nullable=False, max_length=32, origin="human"
    ),
    FieldIR(
        "last_updated_at",
        ProducedBy.DB_GENERATED,
        "timestamptz",
        nullable=False,
        default="now()",
        origin="human",
    ),
    FieldIR("ingest_metadata", ProducedBy.CONSUMER_INJECTED, "jsonb", nullable=True, origin="human"),
)

_DECIMAL_BASES = frozenset({"decimal"})


def _business_field(col: dict[str, Any], proposal: dict[str, Any]) -> FieldIR:
    """Build one mapping-produced business FieldIR from a profiled column + (optional)
    LLM proposal. produced_by is ALWAYS mapping_produced and origin is ALWAYS inferred,
    regardless of the model output (by construction); the curated proposals (name,
    enum vocabulary, pii) ride as flagged inferred values, never decided."""
    base = str(col["base"])
    inline_type: TypeIR | None = None
    if base in _DECIMAL_BASES:
        inline_type = TypeIR(base="decimal", precision=col.get("precision"), scale=col.get("scale"))
    # The LLM may normalize the name and propose an enum vocabulary / pii; absent a
    # proposal the deterministic profiler facts stand. enum_candidate keeps a
    # low-cardinality column a flagged CANDIDATE (base type stays str), per the plan.
    name = str(proposal.get("canonical_name") or col["name"])
    enum_candidate = tuple(proposal.get("enum_values") or col.get("distinct_values") or ())
    return FieldIR(
        name=name,
        produced_by=ProducedBy.MAPPING_PRODUCED,
        type_ref=base,
        nullable=bool(col["nullable"]),
        mandatory=False,  # curated: never auto-decided, flagged via origin inferred
        max_length=col.get("max_length") if base == "str" else None,
        inline_type=inline_type,
        origin="inferred",
        pii=proposal.get("pii"),
        display_name=proposal.get("display_name"),
        description=proposal.get("description"),
        enum_candidate=enum_candidate,
        provenance=FieldProvenance(
            introduced_in=1,
            source_headers=tuple(col.get("source_headers", ())),
            present_in_files=int(col.get("present_in_files", 0)),
            total_files=int(col.get("total_files", 0)),
            rows_profiled=int(col.get("rows_profiled", 0)),
        ),
    )


def assemble_draft_ir(
    vertical: str,
    table_key: str,
    profile_payload: dict[str, Any],
    proposal_payload: dict[str, Any],
) -> SchemaIR:
    """Assemble a draft SchemaIR: injected system profile + inferred business fields.

    ``profile_payload`` = ``{"columns": [<profiled column dict>, ...]}`` (deterministic
    facts). ``proposal_payload`` = ``{"proposals": [<per-column proposal dict>, ...]}``
    (LLM, may be empty on degrade). Proposals are matched by ``source_column`` to a
    profiled column; an unmatched proposal is ignored (a model cannot introduce a
    field). ``natural_key`` is left empty (undecided, ratified in A4).
    """
    proposals_by_col: dict[str, dict[str, Any]] = {}
    for raw in proposal_payload.get("proposals", []):
        source = raw.get("source_column")
        if isinstance(source, str):
            proposals_by_col[source] = raw

    business: list[FieldIR] = []
    for col in profile_payload.get("columns", []):
        proposal = proposals_by_col.get(str(col["name"]), {})
        business.append(_business_field(col, proposal))

    table = TableIR(
        key=table_key,
        template_type="snapshot",
        semantics="merge_upsert",
        sink=f"canonical.{table_key}",  # retail namespace (A2); a proposal, not load-bearing in a draft
        natural_key=(),  # curated: A3 never decides it; a human sets it in A4
        fields=(*SNAPSHOT_SYSTEM_PROFILE, *business),
    )
    return SchemaIR(
        vertical=vertical,
        schema_version=1,
        status="draft",
        system_profile="dis.v1",
        types={},
        enums={},
        tables=(table,),
    )


def validate_fresh_draft(schema: SchemaIR) -> None:
    """The ASSEMBLY-TIME invariant: a freshly assembled draft (fail loud, rule 4).

    Every field origin is inferred or human; every mapping_produced (business) field is
    origin inferred (so its curated attributes are flagged, never decided); every
    non-mapping_produced (injected system) field is origin human (locked). Raises
    ``ValueError`` (the tools/codegen convention) on any violation.

    This is the LOGICAL INVERSE of the publish gate (``ratify_violations`` /
    ``assert_ratified_for_publish``): once a human ratifies a curated field its origin
    flips to ``human``, which this check rejects. So it is for assembly-time use ONLY
    and must never be run at publish.
    """
    for table in schema.tables:
        for f in table.fields:
            if f.origin not in ("inferred", "human"):
                raise ValueError(f"{table.key}.{f.name}: origin must be inferred|human, got {f.origin!r}")
            if f.produced_by is ProducedBy.MAPPING_PRODUCED:
                if f.origin != "inferred":
                    raise ValueError(
                        f"{table.key}.{f.name}: mapping_produced field must be origin inferred, "
                        f"got {f.origin!r} (A3 never decides a curated value)"
                    )
            elif f.origin != "human":
                raise ValueError(
                    f"{table.key}.{f.name}: injected system field must be origin human (locked), "
                    f"got {f.origin!r}"
                )


def is_curated_bearing(field: FieldIR, table: TableIR) -> bool:
    """Whether a field carries a curated attribute that requires human ratification.

    DERIVED from the field's CURRENT content at call time (never a cached flag), so a
    field newly marked mandatory or given an enum candidate via an edit is re-evaluated
    at publish: curated-bearing iff it is a ``natural_key`` member, OR ``mandatory``, OR
    it carries an ``enum_candidate`` vocabulary, OR it has a non-trivial ``pii`` class
    (IR spec section 5). A4 PR1 ratifies at FIELD granularity; section-5's per-attribute
    origin is deferred (it would need per-attribute origin in the IR model).
    """
    return (
        field.name in table.natural_key
        or field.mandatory
        or bool(field.enum_candidate)
        or (field.pii is not None and field.pii != "none")
    )


def ratify_violations(schema: SchemaIR) -> list[str]:
    """The SINGLE source of truth for whether a draft is publishable.

    Returns the reasons it is not ratified (empty == publishable): any curated-bearing
    field still ``origin: inferred``, and any ``merge_upsert`` table whose ``natural_key``
    is empty (unset == not human-ratified). The members of a non-empty natural_key are
    themselves curated-bearing, so a natural key that "looks set" but whose member fields
    are still inferred is rejected by the per-field check. (IR spec section 5 publish gate.)
    """
    violations: list[str] = []
    for table in schema.tables:
        for f in table.fields:
            if is_curated_bearing(f, table) and f.origin != "human":
                violations.append(
                    f"{table.key}.{f.name}: curated attribute still origin: inferred (needs ratification)"
                )
        if table.semantics == "merge_upsert" and not table.natural_key:
            violations.append(f"{table.key}: merge_upsert table has no ratified natural_key")
    return violations


def assert_ratified_for_publish(schema: SchemaIR) -> None:
    """Raise ``ValueError`` if the draft is not ratified for publish.

    Wraps :func:`ratify_violations` (the single check) so a raising caller (the draft
    store's freeze backstop) and a list-returning caller (the publish handler, for a
    clean 4xx with the reasons) share ONE implementation. Distinct from
    :func:`validate_fresh_draft` (the assembly invariant); never run that at publish.
    """
    violations = ratify_violations(schema)
    if violations:
        raise ValueError("draft is not ratified for publish: " + "; ".join(violations))


def _field_to_dict(f: FieldIR) -> dict[str, Any]:
    out: dict[str, Any] = {"name": f.name}
    if f.inline_type is not None:
        out["type"] = {
            "base": f.inline_type.base,
            "precision": f.inline_type.precision,
            "scale": f.inline_type.scale,
        }
    else:
        out["type"] = f.type_ref
    if f.max_length is not None:
        out["max_length"] = f.max_length
    out["nullable"] = f.nullable
    if f.default is not None:
        out["default"] = f.default
    out["mandatory"] = f.mandatory
    out["produced_by"] = f.produced_by.value
    out["origin"] = f.origin
    if f.pii is not None:
        out["pii"] = f.pii
    if f.enum_candidate:
        out["enum_candidate"] = list(f.enum_candidate)
    if f.provenance is not None:
        out["provenance"] = {
            "introduced_in": f.provenance.introduced_in,
            "source_headers": list(f.provenance.source_headers),
            "present_in_files": f.provenance.present_in_files,
            "total_files": f.provenance.total_files,
            "rows_profiled": f.provenance.rows_profiled,
        }
    return out


def emit_draft_ir(schema: SchemaIR) -> str:
    """Render the draft SchemaIR to YAML (the section-3 handoff artifact)."""
    table = schema.tables[0]
    doc: dict[str, Any] = {
        "vertical": schema.vertical,
        "schema_version": schema.schema_version,
        "status": schema.status,
        "system_profile": schema.system_profile,
        "tables": [
            {
                "key": table.key,
                "template_type": table.template_type,
                "semantics": table.semantics,
                "sink": table.sink,
                "natural_key": list(table.natural_key),
                "fields": [_field_to_dict(f) for f in table.fields],
            }
        ],
    }
    return yaml.safe_dump(doc, sort_keys=False)
