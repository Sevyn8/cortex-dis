# `tools/codegen` — Atlas IR-to-artifacts generator

The deterministic generator that turns a ratified Atlas canonical-schema IR into the
artifacts DIS consumes. Logical ownership stays with Cortex/Atlas (ADR-ATLAS-001
decision 2 as amended); the generator binary is hosted here because it imports the
Python `dis-canonical` models to reconcile and emits Python, and Cortex is a
TypeScript monorepo with no Python toolchain. The frozen IR document is the
cross-repo contract.

## What it does (phase A1)

From one IR table it emits, deterministically:

- `render_model` — the `dis-canonical`-equivalent Pydantic model (closed,
  `extra="forbid"`, referencing the shared constrained-type aliases and enums).
- `render_provenance` — the `dis_validation` provenance partition (the five-way
  classification, bucketed from each field's `produced_by`).
- `render_ddl` — the IR-EXPRESSIBLE DDL SUBSET only: table + columns + the
  natural-key COALESCE-sentinel unique index + the `<> ''` sentinel CHECKs.

A1 is proven against the single retail table `store_sku_current_position`, in memory,
no DB: regenerate from `fixtures/retail_store_sku_current_position.ir.yaml` and
reconcile to the live model + partition with zero regression (see
`tests/unit/test_generate_store_sku_current_position.py`).

## A1 emits no runnable SQL

`render_ddl` returns an in-memory `DdlSubset`; `DdlSubset.to_partial_sql` renders a
banner-marked `.sql.partial` fragment that is deliberately incomplete and cannot be
applied as a migration. The full, runnable, reconciled DDL is an **A2** artifact,
gated by generated-DDL-vs-live-schema reconciliation with a DB.

## Deferred items (NOT patched in A1 generator code)

These are IR or generation gaps that A1 deliberately does not work around. A2's
acceptance (the generated DDL reconciles to the live schema, with a DB) is the gate
that forces each one open. Until then the live hand-authored DDL in `schemas/postgres/`
stands.

### DDL-fidelity gaps (A2: must be reconciled against the live schema with a DB)

- `COLLATE "C"` on the string columns. The IR carries no collation.
- Value-range CHECKs (`ck_sscp_*_non_negative`, `ck_sscp_expiry_confidence_range`).
- Cross-field CHECKs (`ck_sscp_expiry_triple_pairing`,
  `ck_sscp_promo_identifier_requires_price`).
- The RLS enable/force and the `tenant_isolation` policy.
- The `last_updated_at` BEFORE UPDATE trigger and its function.
- The secondary indexes (`ix_sscp_*`).
- The `COMMENT ON TABLE` / `COMMENT ON COLUMN` text.
- The constraint/index naming convention (the abbreviated `sscp` prefix); A1 emits a
  deterministic `uq_<table_key>_natural_key` name instead.

### Type-grammar gaps (A2: reconcile, and close in a later IR rev)

- `min_length` / fixed-`char(n)`: the section-3 field grammar lists `max_length` and
  `precision`/`scale` but not `min_length`. A1 expresses `currency` (`char(3)`,
  `CurrencyCode`) via a `types` entry with `min_length: 3, max_length: 3`; the field-
  level grammar should gain `min_length` (or a `char` base) in a later IR rev.
- `jsonb` and `smallint` base types are mapped by the generator (`jsonb -> dict[str,
  Any]`, `smallint -> int`); confirm these base-type names in a later IR rev.

## Out of A1 scope (later phases)

- The forward-only Alembic migration emission.
- Events and `signal_history` (A1 is one snapshot table; `signal_history` stays
  hand-authored — it is daily-compute output with no `mapping_version_id`).
- The consumer sink/namespacing generalization (A2).
- Inference, CSVs to draft IR (A3).
- The override editor and console surfaces (A4).
- The CM publish gate and tenant-to-vertical binding (A5).

## Watch item: entity-key alias coupling (D37)

The IR's `type: uuid` / `type: int` reproduce the entity-key and provenance fields
(tenant_id, store_id, trace_id, mapping_version_id) faithfully ONLY while dis_core
keeps these as plain aliases (TenantId = UUID, MappingVersionId = int). If D37 ever
promotes them to NewType, type_signature will correctly diverge and the prover goes
red. The fix at that point is an IR alias-reference type plus a generator update; it
is not an A1 defect. Self-closing: the prover is the gate that surfaces it.
