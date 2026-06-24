"""A1 prover: regenerate retail's store_sku_current_position from its IR and reconcile
to the live dis-canonical model, in memory, no DB.

Mirrors the assertion shape of
libs/dis-canonical/tests/integration/test_schema_reconciliation.py, but model-to-model
(no Postgres), so it stays green on a bare ``make test``.

Three assertions, per the A1 acceptance:
1. the generated model equals the live ``StoreSkuCurrentPosition`` on field set,
   per-field constrained-type signature, and required-vs-Optional;
2. the generated provenance partition equals the live five-way partition;
3. the generated partition + the existing labels raise neither ``FieldCatalogDriftError``
   nor ``SuiteDriftError`` (the real guards, run against the generated partition passed
   as an argument; no process-global state is mutated).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from dis_canonical import StoreSkuCurrentPosition
from dis_codegen.generate import render_ddl, render_model, render_provenance
from dis_codegen.ir import TableIR, load_ir
from dis_codegen.reflect import type_signature

# dis-ui-server is a service and ships no py.typed marker, so a strict cross-package
# import is untyped. These two symbols are only exercised at runtime in the drift
# assertion; the localized ignore keeps the gate green without modifying the service.
from dis_ui_server.catalog.field_catalog import _assert_no_drift  # type: ignore[import-untyped]
from dis_ui_server.catalog.labels import SNAPSHOT_LABELS  # type: ignore[import-untyped]
from dis_validation import PROVENANCE
from dis_validation.provenance import assert_partition_consistent

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "retail_store_sku_current_position.ir.yaml"


def _table() -> TableIR:
    schema = load_ir(_FIXTURE)
    assert schema.tables, "fixture IR has no tables"
    return schema.tables[0]


def _import_from_source(source: str, module_name: str, path: Path) -> ModuleType:
    """Write generated source to a scratch path and import it as a module."""
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so Pydantic can resolve the generated module's forward
    # refs (the emitted code uses ``from __future__ import annotations``) via its
    # module globals, exactly as a normally-imported module would.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_model_reconciles_to_live(tmp_path: Path) -> None:
    schema = load_ir(_FIXTURE)
    table = schema.tables[0]
    module = _import_from_source(render_model(table, schema), "gen_sscp_model", tmp_path / "model.py")
    generated: Any = module.StoreSkuCurrentPosition
    live = StoreSkuCurrentPosition

    generated_fields = set(generated.model_fields)
    live_fields = set(live.model_fields)
    assert generated_fields == live_fields, {
        "missing_from_generated": sorted(live_fields - generated_fields),
        "extra_in_generated": sorted(generated_fields - live_fields),
    }

    for name in live.model_fields:
        gen_field = generated.model_fields[name]
        live_field = live.model_fields[name]
        assert type_signature(gen_field) == type_signature(live_field), f"type signature drift on {name}"
        assert gen_field.is_required() == live_field.is_required(), f"required-ness drift on {name}"

    assert generated.model_config.get("extra") == "forbid"


def test_generated_provenance_matches_live_partition(tmp_path: Path) -> None:
    table = _table()
    module = _import_from_source(render_provenance(table), "gen_sscp_prov", tmp_path / "prov.py")
    generated: Any = module.PROVENANCE
    live = PROVENANCE[StoreSkuCurrentPosition]

    assert generated.consumer_injected == live.consumer_injected
    assert generated.db_generated == live.db_generated
    assert generated.compute_owned == live.compute_owned
    assert generated.mapping_produced == live.mapping_produced
    assert generated.enrichment_produced == live.enrichment_produced


def test_generated_partition_passes_both_drift_guards(tmp_path: Path) -> None:
    table = _table()
    module = _import_from_source(render_provenance(table), "gen_sscp_prov_guard", tmp_path / "prov2.py")
    generated: Any = module.PROVENANCE

    # SuiteDriftError seam: the five-way partition drift check, run against the
    # generated partition as an argument (no PROVENANCE global mutation). Raises on
    # any mismatch; must not raise here.
    assert_partition_consistent(StoreSkuCurrentPosition, generated)

    # FieldCatalogDriftError seam: the both-directions label-vs-derivation check,
    # run against the generated mapping-produced set and the existing snapshot labels.
    # Raises on drift; must not raise here.
    _assert_no_drift(StoreSkuCurrentPosition, generated.mapping_produced, set(SNAPSHOT_LABELS))


def test_generated_ddl_is_the_partial_nonrunnable_subset(tmp_path: Path) -> None:
    schema = load_ir(_FIXTURE)
    table = schema.tables[0]
    ddl = render_ddl(table, schema)

    # Every canonical column is present (set equality with the live model).
    assert {name for name, *_ in ddl.columns} == set(StoreSkuCurrentPosition.model_fields)

    # Natural-key COALESCE-sentinel unique index: tenant_id prefix, NOT NULL members
    # bare, nullable members COALESCE-wrapped.
    index = ddl.natural_key_unique_index
    assert "(tenant_id, store_id, sku_id, COALESCE(sku_variant, ''), COALESCE(sku_lot_batch, ''))" in index

    # Sentinel CHECKs on exactly the nullable natural-key members.
    assert set(ddl.sentinel_checks) == {"sku_variant <> ''", "sku_lot_batch <> ''"}

    # The emitted artifact is a banner-marked, non-runnable partial. A1 emits no .sql.
    partial = ddl.to_partial_sql()
    first_line = partial.splitlines()[0]
    assert first_line.startswith("--")
    assert "NOT A MIGRATION. NOT RUNNABLE." in partial
    scratch = tmp_path / "store_sku_current_position.sql.partial"
    scratch.write_text(partial)
    assert scratch.suffix == ".partial"
