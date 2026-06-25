"""Atlas IR-to-artifacts generator, phase A1 core.

Reads a canonical-schema IR (the rev-2 shape: spec sections 3, 4, 5) and emits,
deterministically, the artifacts DIS consumes for one canonical table:

- the ``dis-canonical``-equivalent Pydantic model (closed, ``extra="forbid"``,
  referencing the shared constrained-type aliases and enums);
- the ``dis_validation`` provenance partition (the five-way classification);
- the IR-expressible DDL subset (table + columns + the natural-key
  COALESCE-sentinel unique index + the ``<> ''`` sentinel CHECKs).

A1 scope is one snapshot table, proven against the live ``StoreSkuCurrentPosition``
in memory, no DB. See README.md for the deferred DDL-fidelity and type-grammar
items that A2 (generated-DDL-vs-live-schema reconciliation, with a DB) un-parks.
"""

from __future__ import annotations

from dis_codegen.draft_ir import (
    SNAPSHOT_SYSTEM_PROFILE,
    assemble_draft_ir,
    assert_ratified_for_publish,
    emit_draft_ir,
    is_curated_bearing,
    ratify_violations,
    validate_fresh_draft,
)
from dis_codegen.generate import DdlSubset, render_ddl, render_model, render_provenance
from dis_codegen.ir import FieldIR, FieldProvenance, ProducedBy, SchemaIR, TableIR, TypeIR, load_ir
from dis_codegen.reflect import type_signature

__all__ = [
    "SNAPSHOT_SYSTEM_PROFILE",
    "DdlSubset",
    "FieldIR",
    "FieldProvenance",
    "ProducedBy",
    "SchemaIR",
    "TableIR",
    "TypeIR",
    "assemble_draft_ir",
    "assert_ratified_for_publish",
    "emit_draft_ir",
    "is_curated_bearing",
    "load_ir",
    "ratify_violations",
    "render_ddl",
    "render_model",
    "render_provenance",
    "type_signature",
    "validate_fresh_draft",
]
