# Slice 14b: mapping + store data endpoints (dis-ui-server)

First data-endpoint slice on the 13a foundation. Five endpoints serving the mapping-template
workflow and the store list. This doc is goal-level: the API design, the request/response
shapes, the cache-vs-fetch choice, and the best implementation are CC's to design in plan
mode and show for review before any code. The endpoints, their data sources, auth posture,
and the constraints below are fixed.

## Depends on

- Slice 13a (foundation), built and pushed: the FastAPI app, the auth seam
  (`get_current_identity` / `require_tenant` / `require_ops`, dev-stub verifier), the
  `dis-core` error to HTTP envelope (§2.3 shape), structured logging, `dis-rls` per-tenant
  session wiring, the SQLAlchemy ORM declarative base, and the `/api/v1` prefix (probes at
  root). These endpoints are the first to hang off this base and the first to use the ORM
  layer for real (D67).
- Slice 14a (template grain + RLS ON), built and pushed: `config.source_mappings` now keys
  the mapping grain by `(tenant_id, source_id, template_id)` (D68), is RLS ON with the
  single-GUC `app.tenant_id` policy (D69), enforces one ACTIVE per
  `(tenant, source, template_id)`, sequences `version_seq_per_source` per template, and
  enforces `template_name` unique per `(tenant, source)` among non-deprecated rows via the
  EXCLUDE constraint. `template_id` is minted at DRAFT creation, which this slice does.
- Slice 5 (`dis-mapping`), built: the `SourceMapping` model and the four-stage rule
  vocabulary (`{version, rename, normalize, cast, derive}`, D49). The create/edit endpoints
  write `mapping_rules` in this shape and validate against this engine contract.
- Slice 3 (`dis-core`) and `dis-canonical`: the canonical models that the
  template-mapping-fields catalog derives its structural facts from.
- Slice 7 (Mirror Sync) + D55: `identity_mirror.stores` is populated and carries
  `store_code`. The store-list endpoint reads it.
- Decisions this slice honours: D67 (ORM/declarative layer for this service's CRUD, executed
  only through `rls_session`), D68 (template grain), D69 (config RLS ON), D17 (DRAFT →
  STAGED → ACTIVE → DEPRECATED lifecycle; versions immutable once past DRAFT), D49
  (`mapping_rules` shape), D41 (`identity_mirror` is RLS-OFF, so the store endpoint scopes by
  `tenant_id` in-query, not via RLS), D26 (BFF; this slice exercises the
  `config.source_mappings` write scope).
- Decision to REGISTER (operator assigns the number at the commit gate): the store-list
  endpoint's tenant isolation is enforced by an in-query `tenant_id` predicate, NOT by RLS,
  because `identity_mirror` is RLS-OFF (D41). This is a known weak link (no database
  backstop; a missing predicate is a leak). Registered with a revisit trigger: bring
  `identity_mirror` under RLS, or an equivalent backstop, before it carries more
  tenant-facing read surface.

## Goal

After this slice, dis-ui-server serves five endpoints under `/api/v1` that let the mapping
UI list a tenant's stores, show the mappable canonical fields, list and read mapping
templates, and create or edit a template. Reads and writes of `config.source_mappings` run
through `rls_session` (tenant from token); the store read scopes in-query (RLS-OFF table);
the field catalog is not tenant-scoped. Response shapes are clean and simple to consume,
designed by CC (not reverse-engineered from the current frontend, which corrects on its
side); CC reads `services/dis-ui` only to avoid designing something incompatible, and never
edits it.

### The five endpoints

a. **`GET /api/v1/stores-onboarded`** — the tenant's onboarded stores. `tenant_id` from the
   token only (no path/query param). Fields per store: `store_id`, `name`, `store_code`,
   `status`, `country`, `timezone`, `currency`, `tax_treatment`, from
   `identity_mirror.stores`. Tenant-facing only (ops cross-tenant is a later endpoint).
   `identity_mirror` is RLS-OFF, so the query carries an explicit `WHERE tenant_id =
   <token tenant>` and that scoping is test-proven (the registered weak link).

b. **`GET /api/v1/template-mapping-fields`** — the catalog of mappable canonical fields the
   operator maps CSV columns to. Identical for every tenant; NOT tenant-scoped, opens no
   `rls_session`. Per-field shape as discussed: the canonical key, an operator-facing
   display name, a section/group label, mandatory flag (must be PROVIDED, by mapping/derive/
   constant, not "a CSV column must point at it"), a friendly datatype, a description, and
   allowed values for choice/enum fields. Structural facts derived from the canonical schema
   / `dis-canonical` models; display labels/descriptions authored in code and merged by key.
   Cache-at-startup vs fetch-per-request is CC's plan-mode recommendation.

c. **`GET /api/v1/mapping-templates`** and **`GET /api/v1/mapping-templates/{template_id}`**
   — list the tenant's templates, and read one for rendering the create/edit workflow. From
   `config.source_mappings`, tenant-scoped via `rls_session`. The detail returns the
   `mapping_rules` in a shape the UI can render (down-rendered from the D49 JSONB if the raw
   shape is too rich).

d. **`POST /api/v1/mapping-templates`** — create a new template. Mints `template_id`
   (UUIDv7), writes a DRAFT `config.source_mappings` row with the operator-set
   `template_name`, the `source_id` it belongs to, and the `mapping_rules`. Through
   `rls_session`. The `template_name` uniqueness EXCLUDE violation returns a clean 409, never
   a 500. **v1 is hand-authored:** the operator supplies the source column names and the
   `source_id` directly in the request; there is no dependency on a parsed sample upload, and
   no source registry is built or required (the `source_id` is validated as well-formed, not
   checked against a registry that does not exist). A sample-driven assist that pre-fills the
   mapping is an additive layer for a later slice, not a prerequisite here. This is a first
   iteration to try and refine.

e. **`PATCH /api/v1/mapping-templates/{template_id}`** — edit an existing template. A DRAFT
   edits in place; an ACTIVE/STAGED template is immutable (D17), so an edit produces a new
   version rather than mutating the live one. CC reconciles the edit semantics against the
   lifecycle in plan mode and shows the resulting behaviour.

(Route-naming note: the requested routes mixed plural `mapping-templates` for GET with
singular `mapping-template` for POST/PATCH. CC normalizes to one consistent convention,
plural collection, and shows it; this is a consistency fix, not a scope change.)

### Principles the plan must honor (CC proposes how; these are constraints, not solutions)

These are the forks where a wrong choice sets a bad pattern for every later API. The plan
resolves each and shows its reasoning; it does not get to skip them.

- **Template vs version.** A `template_id` is a lineage of versions (D68); one lineage has
  many `config.source_mappings` rows across the DRAFT/STAGED/ACTIVE/DEPRECATED lifecycle.
  The API must be explicit about which the routes operate on: a template's current version,
  or template-and-versions as two levels. Whatever it chooses, `GET /{template_id}`,
  `PATCH /{template_id}`, and the list must agree on it.
- **Immutable past DRAFT.** Versions are immutable once they leave DRAFT (D17). PATCH on a
  DRAFT may edit in place; PATCH on a STAGED/ACTIVE version is a new version, not a mutation,
  and the new version chains to its predecessor (`predecessor_version_id`) and takes a
  defined status. The plan states the new version's status and the chaining.
- **Never-default locale.** The `mapping_rules` written must satisfy D49 exactly, including
  the rule that separator/locale args (`parse_decimal`, `parse_integer`, date formats) are
  mandatory and never defaulted or inferred. If the UI-facing rules shape is flatter than
  the stored D49 shape, the plan shows how create/edit obtains the locale info rather than
  manufacturing it, and how reads render the rich shape down. The write path cannot invent a
  locale.
- **One canonical truth.** The canonical field set that endpoint (b) exposes and the set
  that create/edit validates rule targets against must be the same source, not two
  independently derived lists that can drift. Validation is semantic (targets are real
  canonical columns; mandatory canonical fields end up provided by map/derive/constant), not
  just shape-valid against the engine.

### What this slice does NOT do

No promote / reject / shadow-rollout endpoints (the DRAFT → STAGED → ACTIVE transitions are
a separate slice). Consequently this slice does not move a template to ACTIVE, so the 14a
consumer multi-ACTIVE ordering constraint (the streaming consumer's `.first()` lookup, safe
until a second template is ACTIVE under one source) is NOT triggered here; confirm in plan
that create/edit cannot itself produce a second ACTIVE template. No onboarding sample
upload, dry-run, or suggestion flow (the assisted-mapping path is later). No ops cross-tenant
read of stores or templates (the DIS see-all gap). No edit to `services/dis-ui` (READ-ONLY,
absolute; all frontend change is Amit's). No DDL (14a settled the schema).

## Task

Build the five handlers under `services/dis-ui-server/`, the ORM model(s) for
`config.source_mappings`, the read-side access for `identity_mirror.stores`, the
field-catalog builder, and the Pydantic request/response models, on the 13a base. Confirm
the live shapes in plan mode; do not assert them. Decompose in plan mode and show:

0. **Plan-mode grounding (ERROR, not skip).**
   - **Store field availability.** Introspect `identity_mirror.stores` live: confirm which of
     `store_id`, `name`, `store_code`, `status`, `country`, `timezone`, `currency`,
     `tax_treatment` are actually columns. The mirror is a faithful copy of Customer Master's
     `core.stores`; some of these may not be replicated. Any missing field is a surfaced gap
     (adding it reopens Mirror Sync, out of this slice), not a silently dropped field. State
     what exists.
   - **`config.source_mappings` shape and the ORM model.** Derive the live columns,
     constraints, and the `mapping_rules` JSONB shape (D49) the model maps to. The ORM model
     executes only through `rls_session` (D67, hard rule 1); confirm the read-replica vs
     primary routing the README reserves.
   - **`dis-mapping` rule contract.** Confirm the `SourceMapping` shape the create/edit
     `mapping_rules` must be valid against, so a malformed rule set is a clean 4xx, not a
     write of invalid config.
   - **Frontend read (no edit).** Read `services/dis-ui` to see how these screens consume the
     data, enough to design a compatible, simple shape; do not match it field-for-field and
     do not edit it.

1. **API design + shapes (the deliverable for review).** Propose every route, method,
   request body, response body (Pydantic models), status codes, and error mapping, for all
   five endpoints, and show them in the plan. This is the first API slice; the shapes are
   reviewed before code. Follow the house API conventions: tenant from token only; bare
   resource/array responses (no success envelope); the §2.3 error envelope; `/api/v1`
   prefix; the nullable-lookup vs throw-style decision stated per GET; clean snake_case error
   codes.

2. **Reads** (a, b, c): the store list (in-query tenant scoping), the field catalog (derive +
   merge + the cache decision), the template list and detail (via `rls_session`).

3. **Writes** (d, e): create (mint `template_id`, DRAFT row, validate `mapping_rules`,
   EXCLUDE-violation to 409) and edit (DRAFT-in-place vs new-version per lifecycle), via
   `rls_session`. Whether create/edit publishes `mapping.changed` is a plan-mode call against
   the contract (likely only on lifecycle transitions, which are out of this slice).

4. **Tests (same commit).** Tenant isolation is test-enforced on every tenant-scoped
   endpoint, and especially on the store endpoint where RLS gives no backstop: prove a
   token for tenant A cannot read tenant B's stores or templates, and that `tenant_id` is
   sourced only from the token. Prove the EXCLUDE violation surfaces as 409. Prove the field
   catalog is identical across tenants and needs no tenant context. Prove create mints a
   UUIDv7 `template_id` and writes a valid DRAFT.

## Open questions for plan mode

1. Store field availability on the live mirror (Task 0); any absent field is a surfaced gap.
2. Cache-at-startup vs fetch-per-request for the field catalog: CC's recommendation with its
   reason.
3. PATCH edit semantics across the lifecycle: DRAFT in place; ACTIVE/STAGED edit produces a
   new version (or is rejected with guidance). CC shows the chosen behaviour.
4. The template detail `mapping_rules` shape: serve the raw D49 JSONB, or down-render to a
   simpler UI-facing shape. CC recommends.
5. Route-naming normalization (plural collection) shown.

## Acceptance criteria

- All five endpoints serve under `/api/v1`, with CC's reviewed-and-approved shapes.
- `GET /stores-onboarded` returns the token tenant's stores only, scoped in-query (RLS-OFF),
  with the available fields; a token for tenant A cannot see tenant B's stores (test-proven);
  any unavailable field surfaced as a gap, not silently dropped.
- `GET /template-mapping-fields` returns the canonical field catalog, identical across
  tenants, with no tenant context required; structural facts derived from the schema/models,
  labels authored and merged, drift surfaced.
- `GET /mapping-templates` and `/{template_id}` return the tenant's templates via
  `rls_session`; cross-tenant reads return nothing / not-found.
- `POST` creates a DRAFT template with a minted UUIDv7 `template_id`, valid `mapping_rules`,
  and returns a clean 409 on a duplicate `template_name` (not a 500).
- `PATCH` edits per the reviewed lifecycle semantics.
- All `config.source_mappings` access runs through `rls_session`; the ORM executes only
  through it (D67); no raw SQLAlchemy session, no second engine.
- `services/dis-ui` is unmodified.
- The store-endpoint in-query-scoping weak link is registered with its revisit trigger.
- `make check` / lint / mypy clean; tests in the same commit (code-quality rule 3).
