# Slice 14a: source-mappings migration (template grain + RLS ON)

## Depends on

- Slice 1 (bootstrap migration + the `schemas/postgres/` DDL manifest), applied: the live
  `config.source_mappings` table, the Alembic wiring against the admin role, and the
  `current_database()` target guard in `env.py`. 14a authors a delta migration on this
  table AND updates the manifest DDL so the fresh-bootstrap path and the existing-DB delta
  path converge (the 9a lesson: additive migrations have two paths and the fresh one is
  unexercised, so both are authored and the fresh path is rehearsed on a scratch DB).
- Slice 4 (`dis-rls`), built: the per-tenant `rls_session` and the `create_rls_engine`
  posture guard. 14a does not modify `dis-rls`; it makes `config.source_mappings` an
  RLS-enabled table that the existing per-tenant session scopes, the same way it already
  scopes canonical/quarantine.
- Decisions this slice honours: D15 (mapping config lives in `config.source_mappings`,
  versioned), D17 (assisted onboarding with staged rollout; the DRAFT/STAGED/ACTIVE/
  DEPRECATED lifecycle the grain keys must respect), D22 (canonical rows pin
  `mapping_version_id`, the global BIGSERIAL surrogate; this slice does NOT change that
  pin, see Scope), D49 (`mapping_rules` is `{version, rename, normalize, cast, derive}`;
  untouched here), D23 (Alembic for Postgres DDL).
- Decisions this slice must REGISTER (operator assigns the numbers at the commit gate, not
  invented here):
  - The template grain: a `(tenant_id, source_id)` source may carry multiple mappings, one
    per template (e.g. `manual_csv_upload` carrying sales, inventory, pricing). The mapping
    grain becomes `(tenant_id, source_id, template_id)`.
  - `config.source_mappings` RLS ON: the live table is RLS-OFF with a DDL comment "holds
    configuration, not tenant data"; that comment is corrected. The table carries
    `tenant_id` and its rows are per-tenant, so it follows the DIS principle (RLS ON
    wherever `tenant_id` exists) and gets the same single-GUC `app.tenant_id` policy as the
    other tenant-scoped tables. The two-GUC `app.user_type` pattern is Customer-Master-
    replica-only and does not apply here (confirmed live in Slice 13a: every DIS policy
    reads `app.tenant_id` only).
- CLAUDE.md hard rules: 1 (config reads/writes via `dis-rls`, never raw SQLAlchemy; RLS ON
  makes the table conform to this for free). Target-safety invariant: this slice WRITES DDL
  to Postgres and must run against `ithina_dis_db` (5433), never Customer Master (5432).
- Downstream (consequences this slice does NOT build, flagged so the owning slices honour
  them):
  - Slice 8 (upload Phase 1): the upload-session must carry which `template_id` the CSV is
    for, since a source now has multiple active mappings. v1 selection is user-picks (the
    operator chooses the template); frontend-assist and backend-match are later.
  - Slice 10 (streaming consumer): the active-mapping lookup keys on
    `(tenant, source, template_id)`, not `(tenant, source)`. The consumer reads the
    template off `ingress.ready` / the bronze pointer. This slice does not amend the
    consumer; it makes the grain the consumer will key on.
  - The API contract's per-source `active_version` and `version` become per-template; the
    Group 3 mapping reads (Slice 14b) surface per-template versions. Flagged, not edited
    here.

## Goal

After this slice, `config.source_mappings` supports multiple named mapping templates per
source and is RLS-protected, by a single Alembic migration plus the matching manifest DDL
update. Specifically:

- A `template_id` (UUID, NOT NULL, minted UUIDv7 server-side via the dis-core helper at
  DRAFT creation, immutable once set) gives each mapping a stable identity below
  `source_id`. It does NOT replace `mapping_version_id`: the global BIGSERIAL surrogate
  stays the canonical-row pin (D22) and the audit reference; `template_id` is a new grain
  dimension, not a new primary key.
- A `template_name` (text, NOT NULL, the operator-set human label, editable) is unique per
  `(tenant_id, source_id)` so two templates under one source cannot share a name. The
  uniqueness scope across the status lifecycle (all rows vs non-deprecated only) is derived
  from the live status model in plan mode (see open questions); the likely form is a
  partial unique index mirroring how active-uniqueness already works.
- The one-ACTIVE-per-source constraint (`uq_csm_active_per_source`) is rekeyed to one
  ACTIVE per `(tenant_id, source_id, template_id)`, so sales-active and inventory-active
  can coexist under one source.
- The per-source version sequence (`version_seq_per_source`, set by the
  `trg_csm_set_version` trigger) is rekeyed to sequence per
  `(tenant_id, source_id, template_id)`, so each template has its own version lineage
  starting at 1. The column name becomes a slight misnomer (it now sequences per template);
  rename vs leave-with-corrected-comment is a plan-mode call (open question), kept minimal
  to avoid churn into the contract's `version` mapping.
- RLS is enabled (ENABLE + FORCE) on `config.source_mappings` with a single-GUC tenant
  policy (`tenant_id = current_setting('app.tenant_id', true)::uuid`), identical in shape
  to the other DIS tenant tables, so the existing `rls_session` carries isolation with no
  new mechanism.

This is a schema-only slice. It writes DDL and updates the manifest; it adds NO service
code, NO endpoints, NO row-writing logic. The DRAFT-creation write path (minting
`template_id`, setting `template_name`), the GET reads, and the onboarding flow are Slice
14b and onward.

What it does not do: it does not change `mapping_version_id` or any canonical FK (D22
pin stands); it does not amend the streaming consumer's lookup (Slice 10's, flagged above);
it does not touch the upload-session (Slice 8's); it does not build a platform/ops see-all
read of mappings (DIS has no see-all posture; that is a later policy-migration concern, the
same gap recorded in Slice 13a); it does not edit `mapping_rules` shape (D49 stands).

## Task

Author one Alembic migration on `config.source_mappings` and the matching update to
`schemas/postgres/config/source_mappings.sql`, so both the existing-DB delta and the
fresh-bootstrap manifest reach the same end state. Confirm the live table shape and the
existing trigger/index definitions in plan mode rather than asserting them. Decompose:

0. **Plan-mode grounding preconditions (ERROR, not skip).** Derive the live state and
   RAISE in the plan if it is not as assumed:
   - **Consumer-read posture (the RLS-ON gate).** Confirm that the streaming consumer's
     mapping-config read (D6 side-input) already runs inside a tenant-scoped `rls_session`
     (or a path that sets `app.tenant_id`). If it reads `config.source_mappings` OUTSIDE a
     tenant-scoped session, enabling RLS would make that read return zero rows silently
     (the documented zero-rows-on-unset-GUC trap), breaking the consumer. If the consumer
     read is not tenant-scoped, the plan RAISES and surfaces it, rather than flipping RLS
     and shipping a silent break. This is the load-bearing precondition for the RLS-ON half
     of the slice.
   - **Live table shape.** Introspect the live `config.source_mappings` columns, the
     `trg_csm_set_version` trigger body, the `uq_csm_active_per_source` index definition,
     and the live `status` vocabulary/values. The pasted column list is a snapshot; derive
     the authoritative shape from `information_schema` / `pg_get_triggerdef` /
     `pg_indexes`. Assert no column or definition from the snapshot.
   - **Existing rows + NOT NULL.** Both new columns are NOT NULL. Determine what rows exist
     (the seeded default mapping row, any dev rows) and how the migration populates
     `template_id` (mint a UUIDv7 per existing row) and `template_name` (a deterministic
     backfill label) so the NOT NULL add does not fail. State the backfill explicitly; no
     silent default that could mask a wrong value.
   - **dis-core helper surface.** Confirm the UUIDv7 helper (`new_uuid7`) is callable from
     the migration context (or that the migration mints via the DB `uuidv7()` function the
     bootstrap installed); choose one and state which.

1. **Add the columns.** `template_id uuid NOT NULL`, `template_name text NOT NULL`, with the
   backfill from Task 0 for existing rows. `template_id` immutable by convention (no
   trigger needed in 14a; the write path enforces it later).

2. **Name uniqueness.** A unique constraint/index making `template_name` unique per
   `(tenant_id, source_id)`, scoped across the status lifecycle per the open-question
   resolution (likely partial, excluding DEPRECATED, mirroring active-uniqueness).

3. **Rekey active-uniqueness.** Replace `uq_csm_active_per_source` so the one-ACTIVE
   guarantee is per `(tenant_id, source_id, template_id)`. Derive the existing index's
   exact partial-predicate form and preserve it, changing only the key columns.

4. **Rekey the version sequence.** Rewrite `trg_csm_set_version` (and any supporting
   sequence/function) to sequence `version_seq_per_source` per
   `(tenant_id, source_id, template_id)`. Derive the existing trigger body in plan mode;
   change the grain, preserve the start-at-1 and immutability semantics.

5. **Enable RLS.** ENABLE + FORCE ROW LEVEL SECURITY on `config.source_mappings`; add the
   single-GUC tenant policy (`tenant_id = current_setting('app.tenant_id', true)::uuid`,
   same with_check), shape-matched to an existing DIS tenant policy (derive one as the
   template). Correct the table's DDL comment ("holds configuration, not tenant data") to
   reflect RLS ON.

6. **Manifest parity.** Update `schemas/postgres/config/source_mappings.sql` so a fresh
   bootstrap produces the same end state (columns, constraints, trigger, RLS, policy) as
   the delta migration leaves on an existing DB. Rehearse the fresh-bootstrap path on a
   scratch DB (9a lesson), not only the delta path.

7. **Tests (same commit, code-quality rule 3).** Migration is test-backed, not asserted:
   - The delta migration applies cleanly on a DB at the prior head, and existing rows end
     with a valid `template_id` and `template_name` (backfill proven).
   - The fresh-bootstrap path produces the identical end state (the two paths converge).
   - One ACTIVE per `(tenant, source, template_id)` is enforced: two ACTIVE rows for the
     same triple are rejected; sales-active and inventory-active under one source coexist.
   - `version_seq_per_source` sequences per template: two templates under one source each
     start at 1 and increment independently.
   - `template_name` uniqueness per `(tenant, source)` is enforced at the chosen status
     scope.
   - RLS isolation: with `app.tenant_id` set to tenant A, only A's mapping rows are
     visible; the unset-GUC case returns zero rows (the trap made explicit, proving the
     policy is live); a `NOBYPASSRLS` role cannot see across tenants. Model this on the
     existing canonical/quarantine RLS tests.

## Scope

**In:** the two columns + backfill; `template_name` per-source uniqueness; the active-
uniqueness rekey; the version-seq trigger rekey; RLS ENABLE/FORCE + single-GUC policy; the
manifest DDL update; the migration tests above. One migration, one manifest file.

**Out (with where each lands):** any service code, endpoint, or row-write path (Slice 14b
onward); the streaming-consumer lookup amendment to key on template (Slice 10's owning
follow-up); the upload-session template carry (Slice 8); the contract's per-template
`version`/`active_version` surfacing (Slice 14b); a platform/ops see-all read of mappings
(later policy migration, the Slice 13a-recorded gap); any `mapping_rules` shape change
(D49); any change to `mapping_version_id` or canonical FKs (D22 pin stands).

## Open questions for plan mode

1. `template_name` uniqueness scope: all rows, or non-deprecated only (partial index)?
   Lean: non-deprecated, so a name freed by deprecation can be reused, mirroring active-
   uniqueness. Derive against the live status model.
2. `version_seq_per_source` column: rename to reflect per-template semantics, or leave the
   name and correct the comment? Lean: leave the name (renaming ripples into the contract's
   `version` mapping and the consumer; churn for little gain), correct the comment.
3. Backfill `template_name` for existing rows: what deterministic label (e.g. derived from
   `source_id`, or a fixed "default")? Must be unique per `(tenant, source)` to satisfy the
   new constraint on existing data.
4. UUIDv7 mint site for the backfill: the bootstrap-installed DB `uuidv7()` function vs the
   dis-core helper invoked from the migration. Pick one.

## Acceptance criteria

- `config.source_mappings` gains `template_id uuid NOT NULL` and `template_name text NOT
  NULL`; existing rows are backfilled to valid values; both the delta and fresh-bootstrap
  paths reach the same end state (proven on a scratch DB).
- One ACTIVE mapping per `(tenant_id, source_id, template_id)` is enforced; two templates
  under one source can each be ACTIVE simultaneously.
- `version_seq_per_source` sequences per `(tenant_id, source_id, template_id)`, each
  template starting at 1 independently.
- `template_name` is unique per `(tenant_id, source_id)` at the chosen status scope.
- RLS is ON (ENABLE + FORCE) with the single-GUC tenant policy; tenant-scoped reads see
  only the tenant's rows, the unset-GUC case returns zero rows, and a `NOBYPASSRLS` role
  cannot cross tenants; the Task 0 consumer-read precondition passed (or the plan raised).
- `mapping_version_id`, canonical FKs, and `mapping_rules` shape are unchanged (D22, D49).
- The migration ran against `ithina_dis_db` (5433), never Customer Master (5432); target
  safety confirmed in the plan.
- `make check` / lint clean; migration tests ship in the same commit (code-quality rule 3);
  register gaps (the template-grain decision, the RLS-ON decision) logged with their
  operator-assigned D-numbers at the commit gate.
