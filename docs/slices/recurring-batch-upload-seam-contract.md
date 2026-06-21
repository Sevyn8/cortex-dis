# Recurring-batch upload seam: contract spec (UI-defined, for Sanjeev to build)

**Status:** UI-defined contract proposal, internal. The UI defines what it needs from the upload-session path so a recurring CSV batch can reuse an existing template's active mapping without re-onboarding. Sanjeev builds the backend (this is the D68-deferred "upload-session template carry"). This is a `[SHAPE]` item; his platform policy on auth, ingestion triggering, and storage wins where it meets one.

## The user story (why this exists)

A tenant sets up a source once: they upload a sample, review the mapping (field mappings + format rules), and approve it, which becomes a template's active version. After that, the same-shaped CSV arrives in batches on different dates. The user must NOT re-map or re-review each batch. They drop the new batch, it is ingested through the already-active mapping, done. "Map once, feed forever."

## What exists today (confirmed)

- The mapping is persisted per template: `config.source_mappings` at grain `(tenant_id, source_id, template_id)` (D68). A source carries N named templates (sales, inventory, pricing), each with its own version lineage and an active version.
- A template's active version IS the reusable mapping. The 14b endpoints expose it: `GET /api/v1/mapping-templates?source_id=` lists a source's templates with `active_version`; the detail carries the active version's `mapping_rules` (the field mappings + the normalize/format rules).
- The upload-session path exists but is source-only: `POST /api/v1/upload-sessions` + `/confirm` (D36/D54) carry `source_id` only, NO `template_id`. They have no UI call site today.
- D68 explicitly defers "the Slice 8 upload-session template carry." So the seam below does not exist yet.

## The gap (what the UI needs Sanjeev to build)

A way to upload a new batch that targets a specific `(source_id, template_id)` and is ingested through that template's ACTIVE mapping version, with no onboarding sample, no mapping review, no re-approval.

### Proposed: template-aware upload-session

Extend the upload-session create to carry the template, and to declare this is a recurring-batch ingest (reuse active mapping), not an onboarding sample.

`POST /api/v1/upload-sessions` request (proposed additions in bold intent):
- `source_id` (exists today)
- `template_id` (NEW): the template whose active version applies to this batch.
- `intent` (NEW): an explicit marker, e.g. `recurring_batch` (reuse the active mapping, skip onboarding) vs `onboarding_sample` (the existing first-time path). This keeps the two flows unambiguous server-side.

Response: the signed upload URL/target (as today), plus:
- the resolved `mapping_version_id` (the active version that WILL be applied), so the UI can show "this batch will use mapping vN" for confirmation.
- a clear error if the template has NO active version yet (cannot reuse a mapping that was never activated, see lifecycle dependency below).

`POST /api/v1/upload-sessions/{id}/confirm` (exists): triggers ingestion. For a `recurring_batch` session, ingestion applies the template's active `mapping_rules` directly (the csv-ingest-worker / streaming-consumer path), with NO onboarding/review step.

### What the UI will do with this (T4 and the real-mode wiring)

- From a source's templates list or a template detail (the T2 surface), an "Upload new batch" action on a template.
- It creates a `recurring_batch` upload-session for that `(source_id, template_id)`.
- It shows the active mapping that will be applied (read-only by default: "this batch uses [template name] vN, mapping [field summary] with [locale/format rules]"), so the user confirms the right mapping is being reused.
- It uploads the file to the signed target and confirms. No mapping review screen.
- An "edit mapping" escape hatch (optional, later): if the user knows the format drifted, they can branch into a review/new-version flow instead of reusing, but the default is reuse-without-review.

## Honesty / correctness constraints

- The active version is reused verbatim, including its format rules (the locale/normalize declarations from T3). This is the correctness payoff: the locale was declared once at setup and is reused, so a recurring batch cannot silently mis-parse as long as its format matches what was declared. If the batch's actual format drifts from the declared rules, that is a data-quality failure the pipeline should catch (the Data Quality Playbook / quarantine), not something the UI silently re-infers.
- The UI never re-infers or re-declares the mapping for a recurring batch by default; it reuses the active version. Re-declaration is an explicit user choice (the edit-mapping escape hatch), not automatic.

## Dependencies / open questions for Sanjeev

1. Confirm the upload-session gains `template_id` + `intent` (or your preferred shape for "reuse this template's active mapping").
2. Confirm the confirm-step applies the active `mapping_rules` for a recurring-batch session with no onboarding step (the csv-ingest-worker / streaming path consumes the active version).
3. The active-version dependency: a recurring batch needs the template to HAVE an active version. Activation (promote/reject, STAGED to ACTIVE) is a separate deferred backend slice. So the recurring-batch path depends on the activation lifecycle existing. Sequence: activation lifecycle, then recurring-batch reuse.
4. The source-to-store link (Blocker 2): does the recurring-batch ingest need the store resolved (for identity-resolved fields like store_id, and for locale store-attributes)? If so, the source registry / source-to-store binding is a dependency.
5. Machine ingestion auth: a recurring batch may be uploaded by a machine/automation, not an interactive user. How is that authenticated (the machine-auth-for-ingestion question, previously flagged TBD)? The UI path is interactive; the automated path is yours.
6. Idempotency / dedup for re-uploaded batches (the bronze dedup is single-instance-safe today, D66 parked): if the same batch is uploaded twice, what is the expected behavior?

## Division of authority

The UI defines the upload-session shape it needs (template_id + intent + the active-version-to-apply in the response) and consumes it. Sanjeev owns: the endpoint implementation, how confirm triggers ingestion with the active mapping, the machine-auth model, idempotency, and the activation-lifecycle prerequisite. Where the UI shape meets platform policy, his policy wins.
