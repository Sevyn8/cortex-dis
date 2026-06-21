# Slice 9a: Identity correction (precursor to Slice 8 and 9b)

## Depends on

- The seven hand-edited Pub/Sub contract files already committed to the repo at
  this slice's start: the six existing envelopes (`ingress.ready`,
  `ingress.resubmit`, `quarantine`, `pipeline.dlq`, `mapping.changed`,
  `identity.changed`) corrected per D52, plus the new `csv.received` (D54). This
  slice does **not** re-author those JSON files; it verifies them against the live
  repo and the code, and fixes only a regex char-class divergence (see open
  question 1). Treat the committed contracts as the settled spec, not as something
  to re-shape.
- Slice 1 for the applied `identity_mirror` schema, which the column-add migration
  extends. Field shapes are confirmed by read-only introspection of the live
  `ithina_dis_db` (5433) in plan mode, not from the DDL files.
- Slice 4 for `libs/dis-storage` (the GCS path scheme this slice moves to the UUID
  tenant segment) and the canonical-path hard rule 9.
- Slice 7 for the Mirror Sync Consumer (DB-pull), reopened here additively to
  populate the two new mirror columns; its existing upsert path, RLS-off posture,
  and `current_database()` target guard (D41) must not be disturbed.
- Slice 2 for the Identity Service fake and the fixture set (fixtures already pin
  both the internal UUID and the external code per row), updated here so the fake
  returns the UUID alongside the codes.
- Slice 3 for `dis-core` (`errors` root, `ids` UUIDv7 helper, `logging`) and its
  `identifiers` module, which documented the external `t_*`/`s_*` alias collision
  (the D37 split); whether that module changes now that the external identity form
  is retired is open question 7.
- Decisions this slice implements or honours: D37 (Identity Service returns the
  internal UUID; translation lives at Phase 1), D52 (contract identity fields are
  UUID, optional producer-required codes), D53 (GCS path tenant segment is the
  UUID), D54 (the `csv.received` trigger contract), D55 (mirror gains
  `display_code`/`store_code`, copied as-is), D46 (`status`, not `is_active`), D12
  (mirror is a faithful copy with real FKs), hard rules 9 and 10.
- CLAUDE.md hard rules: errors from `dis-core/errors.py`; UUIDv7 via the `dis-core`
  `ids` helper; structured logging binds the four keys; never log PII or raw
  payloads; GCS access via `dis-storage` only (hard rule 9); Pub/Sub envelopes are
  frozen contracts (hard rule 10), so the contract edits are a coordinated change
  already settled by D52, not a single-service improvisation.
- Downstream consumers this unblocks, which size the surface: Slice 8 (Phase 1
  resolves identity to UUID + codes, builds the UUID path, publishes
  `csv.received`) and Slice 9b (the worker trusts `csv.received`). The real Identity
  Service is Slice 13; 9a changes the contract and the fake now, the real
  implementation honours it then.

## Goal

After this slice, the invented `t_*`/`s_*` identity form is gone from the DIS
contracts and code, and the internal UUID is the single load-bearing identity end
to end. Customer Master's `display_code` (tenants) and `store_code` (stores) are the
authoritative readable codes: held in `identity_mirror`, returned by the Identity
Service alongside the UUID, and carried as optional fields on the envelopes. The
canonical GCS path keys its tenant segment on the UUID. Every Pub/Sub example
validates against its corrected schema. The Identity Service contract and the
Slice 2 fake return the UUID plus the codes, so a Phase 1 caller can resolve once
and propagate the UUID downstream.

This is a cross-cutting correction slice, not a vertical feature. It writes no
receiver, no worker, and no UI; it brings the contracts, the path scheme, the
Identity contract and fake, and the identity mirror into agreement on one identity
model (UUID load-bearing, codes readable), so that Slice 8 and Slice 9b can be built
without re-litigating identity. The single durable data-plane change is the
additive `identity_mirror` migration; everything else is contract, library, fake,
and test work.

What it does not do: it does not implement the real Identity Service (Slice 13), the
dis-ui-server `csv.received` publish or the PUT-completion mechanic (Slice 8), the
`csv-ingest-worker` (Slice 9b), or any producer-side enforcement that the optional
codes are always populated (each producer slice owns that when it is built). It
re-authors none of the already-committed contract JSON; it verifies and, only on a
proven divergence, corrects the regex char-class.

## Task

Confirm exact file and directory placement in plan mode; do not invent paths.
Decompose:

1. **`dis-storage` path scheme to the UUID tenant segment.** Change
   `build_object_path` so the tenant segment is the internal tenant UUID, and add
   the inverse `parse_object_path` that recovers the path's components. Correct hard
   rule 9's path text in CLAUDE.md to the UUID form. The lib stays the only place
   GCS paths are built or parsed (hard rule 9).

2. **Verify the `gcs_uri` regex against what `dis-storage` emits.** Derive the exact
   UUID character-class the lib produces and reconcile it against the
   hand-authored `gcs_uri` patterns in `ingress.ready`, `ingress.resubmit`,
   `quarantine`, and `csv.received`, plus the `pipeline.dlq` example path. Fix the
   regex only if introspection shows a genuine divergence; do not re-shape the
   contracts otherwise (open question 1).

3. **Identity Service contract returns the UUID plus codes.** Edit the OpenAPI
   (authoritative) and proto (reference) so the `resolve_*` responses carry the
   internal `tenant_id`/`store_id` UUID alongside the external `display_code`/
   `store_code`. Additive: the change adds the UUID and codes to what is returned;
   it does not remove the external identifiers other consumers may still read (open
   question 2).

4. **Update the Slice 2 Identity fake to match.** The fake returns the UUID and the
   codes from the fixtures (which already carry both). No production resolution
   logic is built here; only the fake is brought into agreement with the corrected
   contract.

5. **Add the mirror columns (additive migration).** A new Alembic migration adds
   `display_code` to `identity_mirror.tenants` and `store_code` to
   `identity_mirror.stores`, both nullable (D55), and updates the
   `schemas/postgres/identity_mirror` DDL so the manifest and the migration agree
   (open question 4). The migration is reversible (`upgrade`/`downgrade`, D23).

6. **Mirror Sync populates the new columns.** Extend Slice 7's DB-pull select and
   upsert to copy `display_code`/`store_code` as-is from Customer Master, update any
   mirror model that reconciles against the live schema, and backfill existing mirror
   rows. Do not disturb the existing upsert semantics, the RLS-off posture, or the
   `current_database()` target guard (D41).

7. **Contract verification test.** Ensure a test asserts every Pub/Sub example
   validates against its schema, including `csv.received`, and that no identity
   field anywhere retains the `t_*`/`s_*` pattern. This is the verification
   instrument for the hand edits; it errors, never skips, if a schema or example
   file is missing (open question 5).

8. **Capture invariants.** Update the touched per-lib and per-service CLAUDE.md
   files (dis-storage path form, Mirror Sync new columns, the Identity fake contract)
   before slice exit, within their line limits.

## Acceptance criteria

1. `build_object_path` produces the UUID tenant segment and `parse_object_path`
   round-trips it (build then parse returns the inputs); hard rule 9's path text
   matches. A test proves the round-trip and that a built path validates against the
   contract `gcs_uri` regex.
2. The `gcs_uri` regex in each carrying contract matches what `dis-storage` emits,
   verified both directions (a real built path passes; a malformed one fails). Any
   divergence from the hand-authored char-class is corrected and noted; if none, the
   verification stands as evidence and nothing changes.
3. The Identity Service contract (OpenAPI + proto) returns the internal UUID plus
   `display_code`/`store_code`, and the Slice 2 fake returns them; a test resolves
   via the fake and asserts the UUID and both codes are present, with the UUID being
   the field a caller writes identity from.
4. An Alembic migration adds `identity_mirror.tenants.display_code` and
   `identity_mirror.stores.store_code` (nullable); live introspection shows both
   columns; `downgrade` removes them cleanly. Target safety: the migration runs
   against `ithina_dis_db` on 5433, never Customer Master on 5432, asserted
   positively (Slice 7 pattern).
5. Mirror Sync selects and upserts the two columns and backfills existing rows; a
   test against the Slice 7 test-CM Postgres harness (D48) shows the codes land in
   the mirror, copied as-is, with the existing upsert, RLS-off posture, and target
   guard intact.
6. A contract test asserts every Pub/Sub example (the six edited plus
   `csv.received`) validates against its schema, and that no identity field retains
   the `t_*`/`s_*` pattern (a scope assertion over the contract files). The test
   errors, never skips, when a schema or example file is absent.
7. All new and changed code raises `dis-core` errors (no raw
   `RuntimeError`/`ValueError`), binds the four log keys where applicable, logs no
   PII or raw payloads, and mints any UUIDs via the `dis-core` `ids` helper. Each
   touched CLAUDE.md records its new invariants before slice exit.
8. `make check` shows no tier regression and the new tests pass.

## Scope boundary

In scope:
- `dis-storage` path scheme to UUID plus `parse_object_path`; hard rule 9 text.
- Verification (and char-class-only correction) of the committed `gcs_uri` regexes.
- The Identity Service contract (OpenAPI + proto) returning UUID + codes, and the
  Slice 2 fake matching it.
- The additive `identity_mirror` migration and DDL update; the Mirror Sync change
  and backfill.
- The contract verification test and the touched CLAUDE.md invariants.

Out of scope (do not let the slice sprawl):
- Re-authoring the contract JSON. The seven files are hand-done and committed; this
  slice verifies them and corrects only a proven regex char-class divergence.
- The real Identity Service implementation. *Slice 13.* 9a changes only the contract
  and the fake; the deferred obligation is that Slice 13's real service must return
  the UUID + codes the contract now promises.
- dis-ui-server's `csv.received` publish and the mechanic by which dis-ui-server
  learns the PUT completed (client callback vs subscribing to GCS finalize).
  *Slice 8 (D54).*
- The `csv-ingest-worker` and its subscription to `csv.received`. *Slice 9b.*
- Producer-side enforcement that the optional `tenant_display_code`/`store_code`
  are always populated when publishing. Each producer slice owns this as it is
  built (Slice 8 for `csv.received`, Slice 10 for `ingress.ready`/`quarantine`, and
  so on); no live producer of these envelopes exists at 9a, so there is nothing to
  enforce yet.
- Creating the local Pub/Sub topic for `csv.received`. Decide placement in plan mode
  (plumbing here, or with the producer/consumer slice); do not build cloud
  notification wiring (a deferred infra trigger). *See open question 6.*
- Any canonical or bronze schema change. The only DDL is the two nullable mirror
  columns. D38 (event-table dedup columns absent) remains Slice 10's.
- Reopening the UUID-versus-code decisions. D52 and D53 stand; this slice implements
  them, it does not relitigate them.

## Constraints

- The migration is additive and reversible (D23): nullable columns, a working
  `downgrade`, an idempotent backfill. Target safety: `ithina_dis_db` on 5433,
  never Customer Master on 5432, asserted positively (Slice 7 pattern); the read of
  Customer Master for the backfill, if any, asserts the CM database on its side
  (D48 harness in tests).
- The migration runs unchanged locally (5433) and against a provisioned Cloud SQL
  DIS instance via a single `alembic upgrade head` job, resolving its connection
  from the environment with the `current_database()` guard (the Slice 1 bootstrap
  pattern). The backfill tolerates an empty `identity_mirror`, so a fresh
  cloud-first run is a clean no-op and the real population happens on the first
  Mirror Sync DB-pull. Provisioning the GCP DIS project is a separate DEFERRED
  Phase 0 item; 9a does not require it (it runs on the local stack).
- Mirror columns are copied as-is from Customer Master (D12, D55); no transformation
  or re-rendering of the codes. The mirror columns are nullable even though
  `display_code` is NOT NULL at source, to copy faithfully without a constraint the
  source does not guarantee across history.
- The contract edits are a frozen-contract change (hard rule 10) already settled by
  D52; CC verifies the committed files and does not re-open their shape. The
  Identity contract change is additive (returns more, removes nothing other
  consumers rely on).
- All GCS path construction and parsing is via `dis-storage` (hard rule 9); the
  UUID tenant segment is the only tenant form in the path.
- Reopening Slice 7 is additive only: the existing upsert, the RLS-off mirror
  posture, and the `current_database()` target guard (D41) are preserved unchanged.
- Errors from `dis-core/errors.py`; UUIDv7 via the `dis-core` helper; structured
  logging binds the four keys; never log PII or raw payloads.
- Load-bearing proofs (the contract-validation test, the mirror-backfill test, the
  migration target-safety assertion) error rather than skip when their inputs or
  backing stores are absent; a silent skip reports green without having run (Slice 4
  and 7 lesson).
- Live contact is the local stack (Postgres on 5433, the Slice 7 test-CM harness,
  the Slice 2 fakes); plan-mode introspection is read-only against `ithina_dis_db`
  on 5433.
- Per-lib CLAUDE.md under 50 lines; per-service CLAUDE.md under 100 lines; new
  invariants captured before slice exit.

## Open questions (for plan mode to resolve)

1. The `gcs_uri` UUID char-class. Derive the exact character-class
   `build_object_path` emits for the tenant segment (lowercase hex with dashes, the
   standard 8-4-4-4-12 form, or something else) and reconcile it against the
   hand-authored regexes in `ingress.ready`, `ingress.resubmit`, `quarantine`, and
   `csv.received`, plus the `pipeline.dlq` example path. Fix the regex only on a
   proven divergence. Load-bearing: a mismatch either rejects valid paths or admits
   malformed ones.
2. The Identity Service return shape. Confirm the live OpenAPI/proto structure for
   the `resolve_*` responses and where the UUID and codes attach (new top-level
   fields, or a nested identity object), keeping the change additive so any consumer
   still reading the external identifier is not broken. Confirm the fake's current
   return and that the fixtures carry the UUID the fake must now return.
3. Mirror Sync column population and posture. Confirm the Customer Master source
   columns (`display_code` NOT NULL, `store_code` nullable) and how Slice 7's
   existing select and upsert extend to them without disturbing the RLS-off posture
   or the `current_database()` guard (D41). Decide the backfill approach for existing
   mirror rows (within the same Mirror Sync run, or a one-off step) and that it is
   idempotent.
4. Migration versus DDL manifest. Confirm whether the
   `schemas/postgres/identity_mirror` DDL files are updated alongside the Alembic
   migration (the manifest-versus-migration relationship from Slice 1) and whether
   any mirror-model reconciliation (a Slice 3 / dis-canonical-style both-directions
   drift guard, if one exists for the mirror) must be updated so the new columns do
   not trip it.
5. The contract verification test. Confirm whether a schema-versus-example
   validation test already exists and where, or whether 9a adds it, and that it
   errors rather than skips when a schema or example is missing. Confirm the
   no-`t_*`/`s_*` scope assertion is expressible over the contract files.
6. The `csv.received` local topic. The contract file is committed; decide whether
   9a also creates the local Pub/Sub topic (for example in `tools/local`) as
   plumbing, or whether topic creation rides the producer (Slice 8) or consumer
   (Slice 9b). Do not build cloud notification wiring here.
7. `dis-core` `identifiers` and the retired external form. Confirm whether the
   Slice 3 `identifiers` module's external `t_*`/`s_*` aliases (the D37 split) change
   now that the external identity form is retired from the contracts: retired,
   repurposed to the `display_code`/`store_code` string form, or left untouched if
   no code depends on them. Verify against the live repo what currently imports
   those aliases before changing anything.
