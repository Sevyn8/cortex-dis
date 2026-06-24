"""Draft-IR assembly/validation/emission + layer (b) assembly + layer (c) diff.

Layer (b) assembly: feed a canned proposal payload and assert every business value
lands origin: inferred with curated attributes flagged (never decided).

Layer (c) structural-diff invariant: profile the realistic retail CSVs (deterministic,
no LLM) and diff the assembled draft against the hand-authored retail IR fixture.
Tolerance ZERO over the mapping_produced partition {field-name set, base-type family,
nullable} and EXACT match on the 9 injected system fields (name, produced_by, nullable,
default; the anti-drift gate). Declared capacity (max_length / precision / scale) is
EXCLUDED from equality (capacity is schema headroom, not data-inferable; tested as a
function in test_profiler.py). The curated layer (natural_key, mandatory, enum vocab,
pii) is excluded from equality and instead asserted present-but-flagged. The enum
carve-out is an EXPLICIT named list, not a blanket skip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dis_codegen.draft_ir import assemble_draft_ir, emit_draft_ir, validate_draft_ir
from dis_codegen.ir import FieldIR, ProducedBy, SchemaIR, load_ir

# Profiler import is a TEST-only cross-package reference (no production dependency).
# dis-ui-server ships no py.typed marker, so the strict import is untyped here.
from dis_ui_server.infer.profiler import columns_to_payload, profile_csvs  # type: ignore[import-untyped]

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CSV_DIR = _REPO_ROOT / "tools" / "codegen" / "tests" / "fixtures" / "retail_csvs"
_CSVS = [_CSV_DIR / "posdump_acme.csv", _CSV_DIR / "inventory_store12.csv"]
_FIXTURE = _REPO_ROOT / "tools" / "codegen" / "fixtures" / "retail_store_sku_current_position.ir.yaml"

# The fixture base types whose profiler equivalent is intentionally different (the
# fixture declares enum; the profiler emits str + a flagged enum candidate). EXPLICIT
# named list, so a field that silently loses its real type cannot slip through.
_ENUM_CARVE_OUT = {"expiry_source"}

# The 6 declared-layer fields A3 cannot infer from a POS export (compute_owned +
# enrichment_produced); absent-by-design, added by a human in A4.
_ABSENT_BY_DESIGN = {
    "yesterday_retail_price",
    "velocity_7day",
    "stock_age_days",
    "unit_cost_trend_30day",
    "attribute_staleness_map",
    "tax_treatment",
}

_SYSTEM_CLASSES = {ProducedBy.CONSUMER_INJECTED, ProducedBy.DB_GENERATED}


def _base_family(f: FieldIR, schema: SchemaIR) -> str:
    """Resolve a field to its base-type family (through the types block / inline type)."""
    if f.enum_ref is not None:
        return "enum"
    base = f.type_ref
    if base in schema.types:
        base = schema.types[base].base
    elif f.inline_type is not None:
        base = f.inline_type.base
    if base in {"smallint", "bigint", "int", "integer"}:
        return "int"
    if base in {"timestamptz", "datetime"}:
        return "datetime"
    return base  # str, decimal, date, bool, uuid, jsonb


def _assemble_retail_draft(proposal_payload: dict[str, Any] | None = None) -> SchemaIR:
    profiled = profile_csvs(_CSVS)
    return assemble_draft_ir(
        vertical="retail",
        table_key="store_sku_current_position",
        profile_payload=columns_to_payload(profiled),
        proposal_payload=proposal_payload or {"proposals": []},
    )


# --- assembly / validation / emission ----------------------------------------------


def test_assembled_draft_validates_and_round_trips_through_the_a1_loader(tmp_path: Path) -> None:
    draft = _assemble_retail_draft()
    validate_draft_ir(draft)  # must not raise
    out = tmp_path / "retail.draft.ir.yaml"
    out.write_text(emit_draft_ir(draft))
    reloaded = load_ir(out)  # the A1 loader reads the draft (provenance round-trips)
    assert reloaded.status == "draft"
    assert reloaded.tables[0].natural_key == ()  # curated: undecided


def test_layer_b_assembly_stamps_inferred_and_flags_curated() -> None:
    # A canned proposal (the LLM half is pinned in test_proposer.py): names normalized,
    # an enum vocabulary and a pii class proposed. Assembly must stamp them inferred/flagged.
    proposals = {
        "proposals": [
            {"source_column": "expiry_source", "enum_values": ["PRINTED", "SCANNED"], "pii": "none"},
            {"source_column": "sku_id", "canonical_name": "sku_id", "pii": "none"},
        ]
    }
    draft = _assemble_retail_draft(proposals)
    fields = {f.name: f for f in draft.tables[0].fields}
    # Every business (mapping_produced) field is origin inferred; system fields are human.
    for f in draft.tables[0].fields:
        if f.produced_by is ProducedBy.MAPPING_PRODUCED:
            assert f.origin == "inferred", f"{f.name} should be inferred"
            assert f.mandatory is False  # curated: never auto-decided
        else:
            assert f.origin == "human", f"{f.name} should be locked human"
    # The enum proposal rides as a flagged candidate, base type stays str (not decided enum).
    assert fields["expiry_source"].enum_candidate == ("PRINTED", "SCANNED")
    assert fields["expiry_source"].enum_ref is None
    assert _base_family(fields["expiry_source"], draft) == "str"


# --- layer (c): structural-diff invariant vs the hand-authored retail IR ------------


def _partition(schema: SchemaIR) -> dict[str, dict[str, FieldIR]]:
    by_class: dict[str, dict[str, FieldIR]] = {"mapping": {}, "system": {}, "other": {}}
    for f in schema.tables[0].fields:
        if f.produced_by is ProducedBy.MAPPING_PRODUCED:
            by_class["mapping"][f.name] = f
        elif f.produced_by in _SYSTEM_CLASSES:
            by_class["system"][f.name] = f
        else:
            by_class["other"][f.name] = f
    return by_class


def test_c_mapping_produced_field_name_set_matches_tolerance_zero() -> None:
    draft = _assemble_retail_draft()
    fixture = load_ir(_FIXTURE)
    assert set(_partition(draft)["mapping"]) == set(_partition(fixture)["mapping"])


def test_c_base_family_and_nullable_match_tolerance_zero_with_named_enum_carve_out() -> None:
    draft = _assemble_retail_draft()
    fixture = load_ir(_FIXTURE)
    d_map = _partition(draft)["mapping"]
    f_map = _partition(fixture)["mapping"]
    for name, ff in f_map.items():
        df = d_map[name]
        assert df.nullable == ff.nullable, f"{name}: nullable {df.nullable} != fixture {ff.nullable}"
        if name in _ENUM_CARVE_OUT:
            # Fixture declares enum; the profiler emits str + a flagged enum candidate.
            assert _base_family(ff, fixture) == "enum"
            assert _base_family(df, draft) == "str"
            assert df.enum_candidate, f"{name} should carry a flagged enum candidate"
            continue
        assert _base_family(df, draft) == _base_family(ff, fixture), f"{name}: base family drift"


def test_c_injected_system_fields_match_live_exactly_anti_drift() -> None:
    draft = _assemble_retail_draft()
    fixture = load_ir(_FIXTURE)
    d_sys = _partition(draft)["system"]
    f_sys = _partition(fixture)["system"]
    assert set(d_sys) == set(f_sys), "injected system field set drifted from the live fixture"
    for name, ff in f_sys.items():
        df = d_sys[name]
        assert (df.produced_by, df.nullable, df.default) == (ff.produced_by, ff.nullable, ff.default), (
            f"system field {name} drifted: hardcoded template must match the live retail system fields"
        )


def test_c_compute_and_enrichment_fields_absent_by_design() -> None:
    draft = _assemble_retail_draft()
    names = {f.name for f in draft.tables[0].fields}
    assert names.isdisjoint(_ABSENT_BY_DESIGN), "A3 must not infer compute_owned/enrichment_produced fields"


def test_c_whole_draft_origin_invariant() -> None:
    draft = _assemble_retail_draft()
    for f in draft.tables[0].fields:
        assert f.origin in ("inferred", "human")
        if f.produced_by is ProducedBy.MAPPING_PRODUCED:
            assert f.origin == "inferred"  # no curated attribute is ever origin human
        else:
            assert f.origin == "human"
    # natural_key is never auto-decided by A3.
    assert draft.tables[0].natural_key == ()


def test_validate_rejects_a_mapping_field_marked_human() -> None:
    import pytest

    draft = _assemble_retail_draft()
    fields = list(draft.tables[0].fields)
    # Corrupt one mapping_produced field to origin human and assert the guard fails loud.
    idx = next(i for i, f in enumerate(fields) if f.produced_by is ProducedBy.MAPPING_PRODUCED)
    from dataclasses import replace

    fields[idx] = replace(fields[idx], origin="human")
    bad = replace(draft, tables=(replace(draft.tables[0], fields=tuple(fields)),))
    with pytest.raises(ValueError, match="origin inferred"):
        validate_draft_ir(bad)
