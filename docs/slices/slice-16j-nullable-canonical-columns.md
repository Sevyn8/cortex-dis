# Slice 16j: Nullable Canonical Columns (unit_cost, product_category)

## Depends on

Slice 16h (write-gate derives from the model) and Slice 16i (mandatory-flag subtracts
enrichment-guaranteed columns). Both shipped (D101, D102). Because both gates and the field-catalog
flag now derive from the canonical model, this slice's nullability change auto-propagates with no
gate or catalog code edit. This is the third and last of the nullable-columns arc (16h derivation,
16i mandatory-flag, 16j nullable columns).

## Goal

Make unit_cost and product_category nullable on canonical.store_sku_current_position, so the hot
path is more permissive at ingest: a snapshot row upserts even when these two are empty, and
downstream decides how to handle the absence. After this, a snapshot mapping that does not supply
unit_cost or product_category classifies COMPLETE and lands a row with those columns NULL.

## Background

Grounding: docs/scratch/nullable-canonical-columns-grounding.md. The two columns are currently
NOT NULL in the live schema and required (non-Optional) in the model. With 16h and 16i in place,
the create gate, the write gate, and the catalog mandatory flag all derive their required sets from
the model, so flipping the model fields to Optional drops these columns from all three
automatically. The only hand-edits are the migration, the model, and the tests that pin the derived
sets. The NULL-safe non-negative CHECK on unit_cost stays valid (a Postgres CHECK passes on NULL).

## Task

- A new Alembic migration (chains after the current head) that drops NOT NULL on
  canonical.store_sku_current_position.unit_cost and product_category, keeping the existing
  NULL-safe non-negative CHECK on unit_cost.
- Sync the schemas/postgres/ DDL reference for the table so fresh-bootstrap and migrated paths
  produce identical schemas (the fresh == migrated invariant).
- The dis-canonical model: both fields become Optional with a None default (coupled to the
  migration by the dis-canonical rule, never Optional a NOT-NULL-without-default column).
- Update the tests that pin the derived required/mandatory sets to the reduced sets, and any dbt
  not-null assumption on these columns.

The exact migration head to chain from, the exact DDL lines, the live current constraints, and the
exact tests to update are derived in plan mode from live evidence (the live schema on 5433, the
migration chain, and grep), not asserted from the grounding.

## Scope

In scope:
- The migration (drop NOT NULL on the two columns; keep the CHECK) and the matching DDL sync.
- The dis-canonical model change (both fields Optional).
- The tests pinning the derived sets that must move to the reduced sets.
- Any dbt model not-null assumption on these two columns.

Out of scope:
- unit_cost / product_category are the only columns changed; no other column's nullability.
- The write-gate derivation (16h) and the mandatory-flag subtraction (16i) are done; this slice
  relies on them and adds no new derivation logic.
- services/dis-ui is read-only, untouched.
- Any partition, role, or policy change beyond the two NOT NULL drops.

## Open questions for plan mode

1. Live reconciliation. Confirm from the live schema on 5433 (not Customer Master on 5432) the
   current NOT NULL status and constraints on unit_cost and product_category, the current migration
   head to chain the new migration from, and the exact DDL file/lines to sync. Confirm the
   non-negative CHECK on unit_cost is NULL-safe (so it is kept, not dropped).
2. fresh == migrated. How the migration and the DDL sync keep a fresh bootstrap and a migrated DB
   at identical schemas (the existing convergence pattern), and which test proves it.
3. Auto-follow confirmation. Confirm that with the model fields Optional, the create gate, the
   write gate (the derived HOT_REQUIRED_FROM_PROJECTION), and the catalog mandatory flag all drop
   these two columns with no code edit, and identify exactly which pinned tests therefore change to
   the reduced sets.
4. Downstream null-tolerance. Identify every reader of these two columns (the dual-write sink, dbt
   models, any completeness/quarantine/validation path) and confirm none breaks when the columns
   arrive NULL, surface any that assumes non-null as a gap, do not fix outside scope.
5. Downgrade. Whether the migration needs a downgrade leg given the D99 posture (downgrade-
   reversibility deferred until staging); state how this migration fits that posture.

## Acceptance criteria

- unit_cost and product_category are nullable on canonical.store_sku_current_position in both a
  fresh bootstrap and a migrated DB (fresh == migrated, test-proven); the NULL-safe non-negative
  CHECK on unit_cost is retained.
- The dis-canonical model marks both fields Optional; the model-vs-schema drift guard passes.
- The create gate, write gate, and catalog mandatory flag all treat the two columns as
  not-required with no gate/catalog code edit (auto-followed from the model), proven by the updated
  pinned tests.
- A snapshot mapping omitting unit_cost and product_category classifies COMPLETE and upserts a hot
  row with those columns NULL (the headline behavior), proven end-to-end.
- No downstream reader breaks on NULL in these columns; any that would is surfaced, not silently
  handled.
- Full suite green; make check, make lint (incl. import-linter), mypy clean; tests ship in the same
  commit.
- services/dis-ui untouched.

## Constraints

- Libs are mechanism, not policy: the gates and flag derive from the model; this slice changes only
  the model/schema, not any derivation logic.
- fresh-bootstrap and migrated paths must produce identical schemas (DDL synced with the migration).
- The dis-canonical coupling rule: a field goes Optional only together with the migration dropping
  its NOT NULL.
- No D-number assigned in the doc; the operator assigns it at commit.
- The grounding (docs/scratch/nullable-canonical-columns-grounding.md) is reference, not authority;
  plan mode reconciles it against live evidence.
