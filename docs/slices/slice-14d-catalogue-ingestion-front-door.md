# Slice 14d: catalogue ingestion front door (dis-ui-server + streaming-consumer)

Stands up a third ingestion packet, the catalogue / current-position snapshot, beside the
existing sales-event and change-event packets. A tenant can map an ERP current-position file
directly to `store_sku_current_position` (the hot table). The slice is goal-level: the API
shapes, the writer wiring, the migration, and the best implementation are CC's to design in
plan mode and show for review before any code. The endpoints, data flow, and the constraints
below are fixed.

## Depends on

- Slice 13a (foundation), built: the FastAPI app, the auth seam (`get_current_identity` /
  `require_tenant` / `require_ops`), the `dis-core` error-to-HTTP envelope, structured logging,
  `dis-rls` per-tenant session wiring, the SQLAlchemy ORM base, and the `/api/v1` prefix
  (probes at root). 14d hangs handlers off this base.
- Slice 14b (mapping + store endpoints), built: `GET /api/v1/template-mapping-fields` (the
  field catalog, built once at startup, served from memory, tenant-free), the create/edit
  template handlers (`POST` / `PATCH /api/v1/mapping-templates`), and the rule-target validator.
  14d extends these; it does not rebuild them.
- Slice 14a (template grain + RLS ON), built: `config.source_mappings` keyed by
  `(tenant_id, source_id, template_id)`, RLS ON. 14d adds one column to this table.
- Slice 5 (`dis-mapping`), built: the `SourceMapping` rule vocabulary the catalogue rules are
  validated against.
- `dis-canonical`: the `StoreSkuCurrentPosition` model and the event models, and the
  `dis-validation` provenance the catalog and validator derive structural facts from.
- The streaming consumer's hot upsert path (the complete-path hot INSERT and the event-time-wins
  arbiter) already exists. 14d reuses it; it does not change the event projection that feeds it.
- Decisions this slice honours: D63 (catalogue/position onboarding is its own lifecycle,
  sequenced before sales; a sale for an unseen SKU waits until that SKU's position lands), the
  one-canonical-truth rule from 14b (the field set the catalog exposes is the same set the
  validator accepts as targets), and the consumer's dual-write design (events project into the
  hot table; the hot table is the upsert side of that path).
- Decisions to REGISTER at the commit gate (operator assigns numbers): (1) `template_type` as a
  stored discriminator on `config.source_mappings`; (2) the catalogue ingestion path as a packet
  parallel to event routing, target = the hot table by construction; (3) `attribute_staleness_map`
  is not a complete arbiter until the deferred collision slice adds event-path stamping;
  (4) the type vocabulary lives as a single in-code definition now, with a later move to a lookup
  table. This slice resolves the carried-limit that named "registry/routing support for catalogue
  targets" as missing.

## Goal

After this slice, a tenant can onboard a current-position / catalogue file: pick the catalogue
template type, map its columns to `store_sku_current_position` fields, and have the consumer
write those rows directly into the hot table on a path parallel to the event projections. The
existing sales and change event paths are unchanged: they still validate, route, and project
into the hot table exactly as before. The catalogue path is purely additive.

The spine of the slice is a template type. Each template now carries a `template_type` set at
creation, which threads through three subsystems from one source of truth: the field catalog
(which field set to serve), the rule-target validator (which canonical table is a legal target
for this type), and the consumer (which write path the resulting rows take). The allowed types
are defined once in code (`snapshot`, `sales`, `inventory_change`, with room to grow) and read
by every consumer of that vocabulary; the existing implicit sale-vs-change distinction is
formalised into this one definition rather than duplicated.

The catalogue write is bootstrap-only in this slice: it CREATEs a not-yet-existing hot row by
direct upsert, reusing the consumer's existing hot statement and arbiter. It does not resolve
what happens when a catalogue write and an event write contend for the same existing row; that
arbitration is a later slice (see Out of scope). To keep that deferral safe, the catalogue write
stamps `attribute_staleness_map` freshness for the time-sensitive attributes it sets, using the
snapshot's event-time, even though nothing arbitrates on those stamps yet.

The endpoint surface:

a. **`GET /api/v1/template-types`** — the allowed template types, read-only, tenant-free, served
   from the single in-code vocabulary (mirrors the `template-mapping-fields` posture). Each type
   carries a key, a display name, and a description, so the UI can offer the user a type to pick.

b. **`GET /api/v1/template-mapping-fields` becomes type-aware** — serves the field set for a
   requested template type. The catalogue type returns the `store_sku_current_position` field
   set (the authored roster, attached); the event types return their existing field sets. The
   per-object shape gains two keys (`sink`, `constraints`) so the user can see which table a
   field lands in and its column constraint; the shape is uniform across all three types' fields.

c. **`POST` / `PATCH /api/v1/mapping-templates` capture the type** — create and edit accept,
   validate, and store `template_type`, and validate the template's rules against the target
   table legal for that type.

## The object shape (one shape across all field sets)

Every field object the catalog returns carries the same keys, in this order, with JSON `null`
(never the string `"null"`) for empty values:

`key`, `display_name`, `section`, `mandatory`, `constraints`, `datatype`, `description`,
`allowed_values`, `max_length`, `sink`

`section` is a within-packet grouping label (the catalogue packet groups its fields as identity /
product / pricing / inventory / expiry / regulatory_status; the event packets keep their
existing grouping). `sink` is the one real canonical table the field lands in for that packet.
`template_type` is the packet axis and is NOT a field key; it parameterises which set is served.
The structural keys (`mandatory`, `constraints`, `datatype`, `allowed_values`, `max_length`) are
derived from the live canonical schema; the authored keys (`display_name`, `description`,
`section`, `sink`) come from the attached roster, merged by key. `mandatory` keeps 14b's
"must be provided by map / derive / constant" meaning, computed by the same rule the event
sections use, not a new rule.

Object kinds (no `role` key; `key` plus `sink` carry intent):
- `sink` set to a canonical table: the field maps to that column.
- `sink` null and `key` is `__ignore__`: the single reserved Ignore sentinel; any source column
  the tenant does not import maps here; many columns can select it. Not an enumeration of unmapped
  columns.
- `sink` null and any other key (e.g. `store_code`): functional, used by the pipeline (store_code
  routes the row to a store) but not stored as a canonical column.

## Principles the plan must honour (constraints, not solutions)

- **Additive, not overwriting.** The catalogue path is built beside the event path. The event
  routing, event validation, and event projection into the hot table are not modified or rerouted.
  The two paths share only the hot upsert statement and its arbiter; the catalogue writer is a
  sibling to the event writer, not a generalisation of it in place. This is test-enforced: the
  event path still validates, routes, and writes exactly as before.
- **One canonical truth, keyed by type.** The field set the catalog exposes for a type and the
  set the validator accepts as targets for that type are one source, not two that can drift. The
  validator question shifts from "is this table a routing target" to "does this rule's target
  match what this template type may write." The catalogue type's target is
  `store_sku_current_position` by construction; the event types keep their event-model routing.
- **One vocabulary, one definition.** The allowed `template_type` values live in exactly one place
  in code, read by the type endpoint, the catalog, the validator, and the consumer. No second copy
  on the frontend (it reads the type endpoint) and none baked separately into any subsystem.
- **Bootstrap-only write, freshness stamped.** The catalogue write CREATEs the hot row and does not
  arbitrate against an existing row. It stamps `attribute_staleness_map` for the time-sensitive
  attributes it sets (event-time as the value), so the deferred collision slice has the freshness
  evidence to arbitrate later without retrofitting it.
- **Type stored, not inferred.** `template_type` is a stored column on `config.source_mappings`,
  not re-derived from the rules each time. The existing implicit sale-vs-change inference is
  formalised into the stored type.

## Task

Build, on the 13a/14b base and the existing consumer hot path: the type vocabulary and its read
endpoint; the `template_type` column and its migration; the type-aware catalog with the catalogue
field set and the uniform object shape; the validator extension; the create/edit type capture; the
catalogue write path in the consumer; and the staleness stamping. Confirm every live shape in plan
mode; assert none from snapshots. Decompose in plan mode and show:

0. **Plan-mode grounding (ERROR, not skip).**
   - **Live schema for `store_sku_current_position`.** Introspect the hot table on the DIS database
     (5433, never Customer Master on 5432): columns, types, nullability, constraints, and any
     enum-like vocabularies, for the fields in the attached roster. Derive `datatype` / `mandatory`
     / `constraints` / `max_length` / `allowed_values` from this, both directions against the
     authored roster (no authored key without a column, no mapping-produced column without a key),
     surfacing any mismatch.
   - **The shared hot write surface.** Confirm the existing hot upsert statement and arbiter the
     catalogue writer will reuse, and what is event-shaped around it (the parts that assume an event
     row / event target model). State precisely what a sibling catalogue writer reuses versus what
     it must not touch, so the event path is provably unchanged.
   - **`config.source_mappings` shape and the migration.** Derive the live columns and where
     `template_type` sits; design the Alembic migration (add the text column, backfill existing
     rows by formalising the current sale-vs-change inference, code-enforced vocab). No enum type.
   - **The single source for the type vocabulary and the implicit discriminator.** Confirm there is
     no existing types list/endpoint to collide with, and identify the current implicit
     discriminator (how sale vs change is told apart today) so the new vocabulary formalises it
     rather than duplicating it.
   - **`currency` provenance.** Settle whether the catalogue file supplies `currency` (treated as
     mapping-produced for the catalogue type) or the consumer injects it store-denormalized; the hot
     column is NOT NULL, so the write path must produce it. State the choice with evidence.
   - **`attribute_staleness_map`.** Confirm the live shape (keys = column name, values = ISO-8601
     UTC timestamps) and the set of time-sensitive attributes the catalogue write should stamp among
     the columns it sets. The map's compute-derived entries are not written by this slice.

1. **API design + shapes (the deliverable for review).** The `template-types` response; the
   type-aware `template-mapping-fields` request (how the type is passed) and the uniform 10-key
   response; the `template_type` field on create/edit request bodies; status codes and error
   mapping. House conventions: tenant context only where the table needs it (the catalog and types
   endpoints are tenant-free; create/edit are tenant-scoped via `rls_session`); the §2.3 error
   envelope; `/api/v1` prefix; clean snake_case error codes.

2. **Migration + vocabulary.** The Alembic migration for `template_type` (text, backfilled,
   code-enforced) and the single in-code vocabulary definition the four subsystems read.

3. **Reads.** The types endpoint; the type-aware catalog (the catalogue field set merged from the
   roster, the two new keys derived for all three types, the `section`/`type` split that separates
   the field-grouping label from the packet axis).

4. **Validator + writes.** The validator extension (catalogue-target legality keyed by type, event
   routing untouched); create/edit capturing and validating `template_type`; the consumer's
   catalogue writer (bootstrap CREATE via the reused hot upsert, routed by type) and the staleness
   stamping. Confirm in plan whether create/edit publishes anything (likely only lifecycle
   transitions, which are out of this slice).

5. **Tests (same commit).** Event path additive and unchanged: a sale/change template still
   validates, routes, and projects into the hot table as before, test-proven (the load-bearing
   guard). Catalogue bootstrap CREATE writes a hot row from a catalogue template. The validator
   accepts `store_sku_current_position` targets for the catalogue type and rejects them for event
   types, both directions. The type endpoint returns the vocabulary; the catalog is served per type
   and is identical across tenants with no tenant context. Create stores `template_type`; the
   migration backfills existing rows to a correct type. The catalogue write stamps the expected
   staleness entries. `currency` is produced by the chosen path.

## Scope

**In:** the type vocabulary + `GET /api/v1/template-types`; the `template_type` column + migration
(backfilled, code-enforced, no enum); the type-aware catalog with the `store_sku_current_position`
field set and the uniform 10-key object shape; the `section`/`type` split; the validator extension
for catalogue targets keyed by type; create/edit type capture and validation; the consumer's
catalogue bootstrap-CREATE writer reusing the existing hot upsert; staleness stamping for the
catalogue write's time-sensitive attributes; settling `currency` provenance; the authored roster
committed as the catalogue section's source; the tests above.

**Out (with where each lands):**
- Snapshot-vs-event overwrite arbitration on an existing row (per time-sensitive attribute via
  `attribute_staleness_map`): the collision slice; trigger is the two paths contending for the same
  existing row. 14d records the map is not a complete arbiter until then.
- Event-path stamping of `attribute_staleness_map`: part of that collision slice (keeps the event
  path untouched here).
- The sale/change path simplification / refactor: a later slice.
- Moving the type vocabulary from in-code to a lookup table: when the set stabilises.
- The frontend note to Amit (the UI sets `template_type` and reads the type endpoint and the
  type-aware catalog): authored after the backend shape lands.
- `regulatory_flag` provenance: stays flagged (nullable, not required).
- Promote / reject / lifecycle-transition endpoints (separate slice); 14d does not move a template
  to ACTIVE.
- Any edit to `services/dis-ui` (frontend is Amit's, READ-ONLY).

## Open questions for plan mode

1. How the template type is passed to `template-mapping-fields` (query param vs path) and whether
   `template-types` stands alone or folds anywhere (CC found no aggregate route; lean standalone).
2. `currency` supplied by the catalogue file vs consumer-injected store-denormalized (Task 0).
3. The exact set of time-sensitive attributes the catalogue write stamps in
   `attribute_staleness_map` (Task 0), among the columns it sets.
4. The migration backfill rule that maps existing sale/change rows to a `template_type` from their
   current implicit discriminator.
5. Whether deriving the event sections' `sink` (`sale_events` / `change_events`) is clean; if
   fiddly, the fallback is to emit `sink` / `constraints` as `null` on the event objects so the
   object shape stays uniform, with real values filled later.

## Acceptance criteria

- The event path is unchanged: a sale/change template validates, routes, and projects into the hot
  table exactly as before, test-proven.
- `GET /api/v1/template-types` returns the in-code vocabulary (key, display_name, description),
  tenant-free, served from memory.
- `GET /api/v1/template-mapping-fields` is type-aware: the catalogue type returns the
  `store_sku_current_position` field set; every object across all types carries the uniform 10 keys
  with JSON `null` for empty values; the catalog is identical across tenants and needs no tenant
  context; the catalog's exposed keys and the validator's accepted targets remain one source.
- The rule-target validator accepts `store_sku_current_position` for the catalogue type and rejects
  it for the event types, both directions; the catalogue target is legal by type, not by adding the
  hot table to event routing.
- `POST` / `PATCH /api/v1/mapping-templates` capture, validate, and store `template_type`; rules are
  validated against the type's legal target.
- The Alembic migration adds `template_type` (text, code-enforced vocab, no enum) and backfills
  existing rows to a correct type; the vocabulary is defined once in code and read by the type
  endpoint, the catalog, the validator, and the consumer.
- The consumer writes a catalogue template's rows into `store_sku_current_position` via the existing
  hot upsert (bootstrap CREATE), routed by `template_type`, and stamps `attribute_staleness_map` for
  the time-sensitive attributes it sets; it does not arbitrate on those stamps.
- `currency` is produced for the hot row by the chosen path (file-supplied or injected).
- The authored roster is committed as the catalogue section's source; `store_code` (functional) and
  `__ignore__` (sentinel) are present.
- `services/dis-ui` is unmodified; no DDL beyond the one additive `template_type` migration.
- `make check` / lint / mypy clean; tests ship in the same commit.
