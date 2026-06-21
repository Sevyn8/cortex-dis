# Mapping Template: Create and Promote Flow

Shared brief for Amit, Sanjeev, and Claude Code.

Purpose: align on the current state of template creation across `services/dis-ui`
and `services/dis-ui-server`, fix the gaps that strand a created template, and
lock the contract for what crosses the wire. AI assistance stays entirely in the
frontend; the backend is never involved in AI calls.

Read sections 1 and 2 first (for Amit and Sanjeev). Section 3 is the contract.
Section 4 is the instruction to Claude Code.

---

## Section 1: Current situation (for Amit and Sanjeev)

The roadblock is not a contract mismatch. Both sides independently shaped
`mapping_rules` to the same structure, and where the shapes can be compared they
agree. The real problem is three missing links in the create path, and we have
each been pointing at a different one assuming the other half exists.

What is already true and working:

- The `mapping_rules` shape agrees on both sides: `{version, rename, normalize,
  cast, derive}`. The frontend type (`mapping-templates.ts:42-48`) mirrors the
  server model `SourceMapping`, which is frozen and `extra="forbid"`
  (`libs/dis-mapping/src/dis_mapping/models/source_mapping.py:42-51`).
- The CSV upload seam is wired end to end and matches field for field: the UI
  sends a multipart `file` plus `template_id` plus `store_code`
  (`csv-uploads.ts:52-58`); the server stream-parses it
  (`upload_stream.py:166-239`), writes to GCS, publishes `csv.received`, and the
  downstream worker and streaming consumer resolve the ACTIVE template and stamp
  `mapping_version_id` on canonical rows. This path is real and tested.
- Template and catalog reads are wired in real mode: `GET
  /api/v1/mapping-templates[/{id}]` and `GET /api/v1/template-mapping-fields`.
- On the server only, template CREATE and PATCH exist, are validated, RLS-scoped,
  and tested, but have no caller from the UI.

The three missing links in the create path:

1. The wizard never sends a template. Go-live calls a fixture
   (`approveSample`, `onboarding.ts:174-180`) rather than the real POST. The real
   client function `createMappingTemplate()` exists, is tested, and has zero call
   sites (`mapping-templates.ts:452-456`). Nothing in the wizard assembles the
   full `{version, rename, normalize, cast, derive}` document from the operator's
   edits (`overrides` + `localeRules` in `MappingReview.tsx:78-91`); only the
   per-column piece `buildNormalizeSpec()` exists (`locale-rules.ts:96-114`).
2. Lifecycle dead-end. The server create yields a DRAFT
   (`repos/mapping_templates.py:130-178`). There is no promotion endpoint, so a
   created template can never reach STAGED or ACTIVE. CSV upload requires ACTIVE
   (`handlers/csv_uploads.py:208-210`). Any ACTIVE template in the database today
   got there outside this flow (seeding).
3. Semantic mismatch at Go-live. The fixture returns `status: 'staged'`
   (`onboarding.ts:179`), implying "approve maps to staged", while the real
   create produces DRAFT. The wizard's implied lifecycle and the server's actual
   lifecycle do not connect.

Net: the pipeline from "an ACTIVE template exists in `config.source_mappings`"
through to canonical rows is real and tested. The pipeline from "operator maps
fields in the wizard" to "an ACTIVE template exists" is fixture-only on the UI
side and missing its promotion endpoint on the server side.

---

## Section 2: Forward path and AI scope (for Amit and Sanjeev)

AI assistance is purely a frontend concern. `dis-ui` owns sample analysis and
the AI-suggested mapping (suggested canonical fields, confidence, transforms) and
calls any AI API directly from the frontend. `dis-ui-server` does not proxy,
relay, or call AI on the frontend's behalf. It does not make sense to round-trip
through the backend just to reach an AI API.

Consequences of this decision:

- The sample is parsed and analyzed in the browser. The CSV sample contents are
  not sent to `dis-ui-server` for analysis.
- The previously assumed server-side onboarding handlers (sample upload, analyze,
  approve) are not needed and are out of scope. The UI's fixture onboarding
  client (`onboarding.ts:140-180`) is replaced by real frontend logic, not by new
  backend endpoints.
- What crosses to the backend is only the finished, operator-approved template:
  a plain `{source_id, template_name, mapping_rules}` document. AI never appears
  on the wire.

The lifecycle is draft, then staged, then active. Create lands as DRAFT.
Promotion moves DRAFT to STAGED and STAGED to ACTIVE. CSV upload binds to the
ACTIVE version. This progression needs server endpoints that do not exist today.

The forward path, in plain terms:

- Frontend: assemble the full `mapping_rules` document from the operator's edits,
  send it via the real create call, then offer promote controls that drive the
  lifecycle to ACTIVE.
- Backend: keep create and edit as they are, and add the promotion endpoints that
  move a template through DRAFT, STAGED, ACTIVE, so a UI-created template can
  reach the state CSV upload requires.

---

## Section 3: The contract (what crosses the wire)

This is the agreed interface. Claude Code should confirm each line against live
code and flag any divergence rather than assume.

Create a template:

- `POST /api/v1/mapping-templates`
- Request body: `{ source_id, template_name, mapping_rules }`
  - `source_id`: string, pattern `^[a-z0-9_]{1,128}$`. Validated well-formed
    only; no source registry exists yet (a known limit).
  - `template_name`: 1 to 200 chars. Duplicate per `(tenant, source)` returns
    409.
  - `mapping_rules`: `{ version, rename, normalize, cast, derive }`. Strict:
    extra keys inside `mapping_rules` are rejected. `normalize` and `derive` are
    lists of `{op, args}`; `cast` is per-field `{type, precision, scale}`.
- Result: a DRAFT version. Response is `MappingTemplateDetail`.
- AI involvement: none. The frontend has already used AI to help the operator
  build `mapping_rules`; only the finished document is sent.

Promote a template (to be built):

- Lifecycle: DRAFT, then STAGED, then ACTIVE. One ACTIVE per `(tenant, source,
  template)` is enforced by a partial unique constraint.
- The endpoint shape, the number of steps, and who can trigger ACTIVE are product
  decisions to lock in the plan (see open decisions below).

Upload a CSV (already wired, included for completeness):

- `POST /api/v1/csv-uploads`, multipart: `file`, `template_id`, `store_code`,
  `Authorization: Bearer`.
- The client names a template lineage by `template_id`, never a version. The
  server resolves the ACTIVE version at upload time and again at consume time.
  `source_id` is taken from the ACTIVE template row, never from the request.

Open product decisions for the plan to surface, not silently resolve:

- Does Go-live create as DRAFT and then require explicit promotion, or should one
  operator action create and stage in a single step?
- Is promotion two steps (DRAFT to STAGED, STAGED to ACTIVE) with an operator
  action at each, or can a template be created directly into STAGED?
- What triggers ACTIVE: a direct operator action, or a gated step after a staged
  or shadow validation period (see D17 if relevant)?
- How should the existing DRAFT-vs-staged semantic mismatch at Go-live be
  resolved so the UI's implied lifecycle and the server's actual lifecycle agree?

---

## Section 4: Instruction to Claude Code

Review the current state of both services against this brief, then produce an
implementation plan and a test plan. Plan mode first. Do not edit `services/dis-ui`
during review; it is owned by Amit. All tooling, lint, format, and type commands
must be scoped to exclude `services/dis-ui`.

Scope of work:

- `dis-ui` (Amit's service): assemble the full `mapping_rules` document
  (`{version, rename, normalize, cast, derive}`) from the wizard's `overrides`
  and `localeRules` state; wire Go-live to the real `createMappingTemplate()`
  call instead of the fixture `approveSample`; add promote controls that drive
  the lifecycle once the server endpoints exist. AI-assisted mapping stays
  frontend-only; do not introduce any backend AI call.
- `dis-ui-server`: build the promotion endpoints that move a template through
  DRAFT, STAGED, ACTIVE, so a UI-created template can reach ACTIVE (which CSV
  upload requires). Keep create and PATCH as they are unless the plan identifies
  a concrete reason to change them. Resolve the DRAFT-vs-staged semantic so the
  two services agree.

Constraints and conventions:

- The live database is authoritative for schema. Derive from the live DB and the
  authoritative DDL, not from file snapshots alone.
- Every load-bearing plan claim must carry inline evidence as `path:line`.
- Surface the open product decisions in Section 3 explicitly. Do not assume an
  answer; flag each as a decision for Sanjeev to lock before execution.
- Cite existing decisions by D-number where relevant (lifecycle, D68 grain, D71
  promotion unblock, D17 staged rollout, D22 / D73 version pinning and lineage).

Deliverables from the plan-mode run:

- A reconciliation of the current code against this brief, confirming or refuting
  each claim with `path:line` evidence.
- An implementation plan split by service, with the frontend and backend pieces
  separable, and the order of work (which service must land first).
- A test plan covering: full `mapping_rules` assembly from wizard state; the real
  create call and its DRAFT result; each promotion transition; the ACTIVE
  precondition for CSV upload; and the end-to-end path from Go-live to a CSV
  upload that resolves the newly created ACTIVE template.
- A flat list of open decisions and any divergence found between the brief and
  the code.
