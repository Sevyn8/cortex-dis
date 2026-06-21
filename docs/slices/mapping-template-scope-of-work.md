# Mapping Template Create and Promote: Scope of Work and CC Prompts

Companion to the create-and-promote brief. This document breaks the work into
discrete units per service, with current-state evidence, build targets, and the
open decisions each unit carries. It ends with two plan-mode prompts to relay to
Claude Code, one per service.

Convention: `path:line` references are from the read-only investigation and must
be re-confirmed against live code during planning. The live database and the
authoritative DDL are the source for schema, not file snapshots.

---

## Part A: dis-ui scope of work (owned by Amit)

The wizard runs Upload, then Review mapping, then Preview, then Go live. Today the
analysis is fixture-driven, operator edits are real screen state, and Go-live
calls a fixture. The work is to make the real path produce a finished template
document, send it, and drive it to ACTIVE. AI assistance stays inside the
frontend throughout; nothing AI-related crosses to the backend.

### WU-UI-1: Real sample analysis (AI in the frontend)

Current state:
- Sample create, fetch, patch, dry-run, and approve are fixture-only and throw in
  real mode: `ensureFixtureMode` at `onboarding.ts:134-138`, guarding
  `createSample` (`:140-146`), `getSample` (`:148-155`),
  `patchSampleMapping` (`:157-166`), `dryRunSample` (`:168-172`),
  `approveSample` (`:174-180`).
- The suggested-mapping shape the UI expects is `SampleAnalysis` / `SampleColumn`
  (`source_col`, `proposed_canonical`, `confidence`, `transforms`, `reasoning`,
  `alternatives`): `onboarding.ts:18-39`.

Build target:
- Parse the uploaded CSV sample in the browser and produce a `SampleAnalysis`
  using a direct frontend AI call (and/or a deterministic heuristic fallback).
- The sample contents are never sent to dis-ui-server. Only the finished template
  is sent, later, by WU-UI-3.

Open decisions:
- Which AI API and how the key and rate limits are handled in the frontend.
- The heuristic fallback when AI is unavailable, and how confidence is surfaced.
- Sample parsing approach in-browser (for example a CSV parser library).

### WU-UI-2: Full `mapping_rules` assembly

Current state:
- Operator edits are held as `overrides` (`Record<sourceCol, {proposed_canonical,
  authoritative}>`) and `localeRules` (`Record<sourceCol, LocaleDeclaration>`):
  `MappingReview.tsx:78-91`. `LocaleDeclaration` carries `format`, `timezone`,
  `decimal_separator`, `thousands_separator`: `locale-rules.ts:69-74`.
- Only one assembly piece exists: `buildNormalizeSpec()` turns a
  `LocaleDeclaration` into a `TransformSpec` (`parse_date` / `parse_datetime` /
  `parse_decimal`): `locale-rules.ts:96-114`.
- No function assembles the full document. Verified absence.

Build target: a pure function taking wizard state plus the canonical-field catalog
and returning a complete `SourceMappingRules` `{version, rename, normalize, cast,
derive}` (`mapping-templates.ts:42-48`):
- `rename`: source column to canonical key, from `overrides.proposed_canonical`.
- `normalize`: per column, a list of `{op, args}` from `localeRules` via
  `buildNormalizeSpec`, plus any enum lookup, whitespace, null-encoding, or casing
  ops the column needs.
- `cast`: per canonical field, `{type, precision, scale}` from the catalog
  datatype, with `precision` and `scale` carried for decimals (for example
  `decimal(12,4)` as shown in the template detail view).
- `derive`: per target field, a list of `{op, args}` for derived columns (for
  example `date_from_datetime` from a source timestamp).
- `version`: the rules-document internal version field. Confirm the correct
  initial value (this is distinct from the per-source sequence the DB assigns).

The assembled document must satisfy the server validation that runs on create:
the four checks are D49 shape, non-empty `rename`, exactly-one-event-model
routing, and mandatory-field coverage (`mapping_validation.py:131-136`). Mirror
these as a client-side proceed-gate so the operator cannot reach Go-live with a
document that will be rejected.

Open decisions:
- The exact normalizer vocabulary the assembler emits now (parse-date,
  parse-decimal, parse-boolean, enum-lookup, currency, whitespace, null-encoding,
  casing), bounded to current need.
- How a derived field is expressed in the wizard UI and carried into `derive`.

### WU-UI-3: Wire Go-live to the real create call

Current state:
- Go-live calls the fixture `approveSample(sampleId)`:
  `MappingReview.tsx:177-185`, which returns a canned `{source_id:
  'manual_csv_upload', mapping_version: 1, status: 'staged'}`:
  `onboarding.ts:174-180`.
- The real client function `createMappingTemplate()` exists, is tested, and has
  zero call sites: `mapping-templates.ts:452-456`; wire shape pinned by
  `mapping-templates.test.ts:199-210`.
- `source_id` is derived (new source) or selected (existing source) at
  `SampleUpload.tsx:45-50` and `:184-198`, via `deriveSourceId`
  (`sources.ts:40-46`), but is not sent today.

Build target:
- On Go-live: assemble (WU-UI-2), then call `createMappingTemplate({source_id,
  template_name, mapping_rules})`.
- Resolve `source_id` from the Upload step. Confirm where `template_name` is
  captured in the wizard and carry it.
- Handle the real responses: 201 returns `MappingTemplateDetail` as a DRAFT; 409
  on duplicate name per (tenant, source); 422 on validation failure.

Open decisions:
- Where the operator names the template versus the source, and which value maps to
  `template_name`.

### WU-UI-4: Lifecycle controls (promote to ACTIVE)

Current state:
- The READ side of the lifecycle is wired in real mode and shows status badges and
  version history (`mapping-templates.ts:417-448`, status enum
  `draft|staged|active|deprecated` at `:21`).
- No activate, stage, or deprecate mutations are wired in the UI.
- The Ingest Data screen gates the Ingest action on `active_version !== null`:
  `IngestData.tsx:141-165`.
- The fixture implied "approve maps to staged"; the real create produces DRAFT.

Build target:
- Promote controls that drive DRAFT to STAGED to ACTIVE by calling the new server
  promotion endpoints (WU-SRV-1), with the result reflected in the template detail
  and Ingest Data screens.
- Resolve the DRAFT-versus-staged semantic so the screen state after Go-live is
  correct and matches the server.

Open decisions (shared with the backend, must agree):
- Whether Go-live creates DRAFT and the operator then promotes, or one action
  creates and stages.
- Whether ACTIVE is a direct operator action or gated behind a staged period.

### WU-UI-5: Retire the fixture onboarding seam for real mode

Build target: replace the real-mode throwers with the real implementations from
WU-UI-1 through WU-UI-4, while keeping fixture mode functional for demo if still
wanted (`mode.ts:6-16`).

### WU-UI-6: Tests (scoped to dis-ui)

- Assembler unit tests per rule family, including enum and decimal cases and
  mandatory-field coverage.
- Create-call wire test, extending `mapping-templates.test.ts:199-210`.
- Go-live happy path plus 409 and 422 handling.
- Promote flows DRAFT to STAGED to ACTIVE.
- Proceed-gate parity with the server's four validation checks.

---

## Part B: dis-ui-server scope of work (owned by Sanjeev)

Create and PATCH exist and are tested. The missing capability is promotion: moving
a template through the lifecycle so a UI-created template can reach the ACTIVE
state that CSV upload requires.

### WU-SRV-1: Promotion endpoint(s)

Current state:
- Create yields a DRAFT: `handlers/mapping_templates.py:152-177`,
  `repos/mapping_templates.py:130-178` with `status=_STATUS_DRAFT`.
- PATCH renames the lineage and edits or mints a DRAFT, with no status
  transitions: `repos/mapping_templates.py:228-319`.
- No promotion path exists in any handler. D71 lifted the gate that blocked
  building it (`docs/decisions.md:1097-1100`).
- Status values are constrained to DRAFT, STAGED, ACTIVE, DEPRECATED by a CHECK
  constraint (authoritative DDL `schemas/postgres/config/source_mappings.sql`).

Build target:
- An endpoint or endpoints to transition a template version's status, RLS-scoped
  exactly like create.
- Define the legal transitions: DRAFT to STAGED, STAGED to ACTIVE, and ACTIVE to
  DEPRECATED on supersede.

Open decisions:
- One endpoint taking a target status, or per-transition routes.
- Whether DRAFT to ACTIVE (skipping STAGED) is permitted.

### WU-SRV-2: The one-ACTIVE invariant on promotion to ACTIVE

Current state: a partial unique constraint enforces one ACTIVE per (tenant,
source, template): `uq_csm_active_per_source` WHERE `status='ACTIVE'`
(migration `0005`, DDL constraint section).

Build target: promoting a version to ACTIVE must, in a single transaction, move
the prior ACTIVE for the same (tenant, source, template) to DEPRECATED, set its
`deprecated_at`, and set `activated_at` on the newly active version. Without this
the constraint rejects the second ACTIVE.

### WU-SRV-3: Transition guards and concurrency

Build target:
- Reject illegal transitions with typed errors from the existing `DisError` root;
  no raw exceptions. 404 for unknown or cross-tenant; 409 for an illegal
  transition.
- Reuse the lock-then-reread serialization the PATCH path already uses
  (`repos/mapping_templates.py:206-254`) so concurrent promotes are safe.

### WU-SRV-4: Lineage and summary correctness

Build target:
- Confirm promotion is a status update on the existing version row, not a new row,
  and that `predecessor_version_id` chains are unaffected.
- Recompute the `MappingTemplateDetail` summary (`latest_version`,
  `active_version`, `staged_version`, `draft_version`) correctly after a
  transition.

### WU-SRV-5: DRAFT-versus-staged semantic

Build target: decide and implement whether create stays DRAFT with a separate
promote-to-staged, or create can optionally land STAGED, and align the result with
the frontend (WU-UI-4). This is the semantic that currently disagrees between the
two services.

### WU-SRV-6: Side effects on transition

Current state: create and upload emit audit via `audit.emit` (for example
`handlers/csv_uploads.py:295-312`). The architecture notes the server publishes a
`mapping.changed` event on mapping CRUD and promotions.

Build target:
- Emit an audit event on each transition.
- Confirm whether promotion should publish `mapping.changed`, given that CSV
  upload and the streaming consumer both resolve the ACTIVE version dynamically at
  run time (`handlers/csv_uploads.py:208-210`,
  `streaming-consumer/.../pipeline/mapping.py:199-208`). Decide whether any
  downstream cache invalidation is needed or whether the event is informational.

### WU-SRV-7: Tests (server side)

- Each transition; illegal-transition rejection.
- The one-ACTIVE invariant, proving the prior ACTIVE auto-deprecates.
- RLS scoping on every transition.
- Concurrency: two promotions racing on the same template.
- End to end: create, promote to ACTIVE, then a CSV upload resolves the newly
  created ACTIVE version (ties to `handlers/csv_uploads.py:208-210`).

---

## Part C: Sequencing

The backend promotion endpoint shape (WU-SRV-1 through WU-SRV-3) is the contract
the frontend promote controls (WU-UI-4) call. Lock that endpoint shape in the
plan first. After it is agreed, both services can build in parallel; the frontend
can develop against the agreed shape before the backend lands.

The create contract (`POST /api/v1/mapping-templates`) and the CSV upload contract
(`POST /api/v1/csv-uploads`) are already settled and wired on the server, so
WU-UI-2 and WU-UI-3 do not depend on new backend work.

---

## Part D: Plan-mode prompts for Claude Code

Run these in plan mode. Each is self-contained. Attach the create-and-promote
brief and this scope-of-work document alongside the prompt.

### Prompt 1: dis-ui plan (run by Amit)

```
Plan mode. Review services/dis-ui against the attached brief and scope of work,
then produce an implementation plan and a test plan for the dis-ui work units
(WU-UI-1 through WU-UI-6). Do not write code in this run.

Confine all work and all tooling, lint, format, and type commands to
services/dis-ui.

Goal: make the Create Template wizard produce a finished mapping_rules document
from the operator's edits, send it via the real createMappingTemplate() call, and
drive the created template through DRAFT, STAGED, ACTIVE using the server's
promotion endpoints. AI-assisted mapping stays entirely in the frontend; the CSV
sample is parsed in the browser and is never sent to dis-ui-server, and no AI call
is routed through the backend.

For each work unit, confirm or refute the current-state claims in the scope
document with path:line evidence from live code, then plan the change. Pay
particular attention to:
- the full {version, rename, normalize, cast, derive} assembler (WU-UI-2) and
  whether it can produce documents that pass the server's four validation checks;
- wiring Go-live to the real create call and handling DRAFT, 409, and 422
  (WU-UI-3);
- promote controls that call the server transition endpoints (WU-UI-4), once their
  shape is agreed.

Surface, do not resolve, the open product decisions: onboarding AI choices, the
normalizer vocabulary to emit now, where template_name is captured, and the
DRAFT-versus-staged semantic shared with the backend.

Deliverables: a current-state reconciliation with path:line evidence; an
implementation plan per work unit with file targets; a test plan covering the
assembler, the create call, Go-live, and the promote flows; and a flat list of
open decisions and any divergence found.
```

### Prompt 2: dis-ui-server plan (run by Sanjeev)

```
Plan mode. Review services/dis-ui-server against the attached brief and scope of
work, then produce an implementation plan and a test plan for the dis-ui-server
work units (WU-SRV-1 through WU-SRV-7). Do not write code in this run.

Do not edit, format, lint, or type-check anything under services/dis-ui; it is
owned by Amit. Scope all tooling commands to exclude it.

Goal: add the promotion capability that moves a template through DRAFT, STAGED,
ACTIVE, so a UI-created template can reach the ACTIVE state that
POST /api/v1/csv-uploads requires. Keep create and PATCH as they are unless the
plan finds a concrete reason to change them.

Derive schema from the live database and the authoritative DDL, not from file
snapshots. Confirm or refute the current-state claims in the scope document with
path:line evidence. Pay particular attention to:
- the promotion endpoint shape and the legal transition set (WU-SRV-1);
- the one-ACTIVE invariant, where promoting to ACTIVE must atomically deprecate
  the prior ACTIVE for the same (tenant, source, template) to satisfy the partial
  unique constraint (WU-SRV-2);
- typed transition guards and the lock-then-reread concurrency pattern already in
  the PATCH path (WU-SRV-3);
- whether promotion should publish mapping.changed, given that CSV upload and the
  streaming consumer resolve the ACTIVE version dynamically at run time (WU-SRV-6).

Cite relevant decisions by D-number (D68 grain, D71 promotion unblock, D17 staged
rollout, D22 and D73 version pinning and lineage).

Surface, do not resolve, the open product decisions: one endpoint versus
per-transition routes, whether DRAFT to ACTIVE may skip STAGED, and the
DRAFT-versus-staged semantic shared with the frontend.

Deliverables: a current-state reconciliation with path:line evidence; an
implementation plan per work unit with file and migration targets; a test plan
covering each transition, the one-ACTIVE invariant, RLS scoping, concurrency, and
the end-to-end create-to-promote-to-upload path; and a flat list of open decisions
and any divergence found.
```
