# Slice 8: CSV upload, Phase 1 (dis-ui-server synchronous endpoint)

One synchronous endpoint on the 13a/14b foundation: the DIS UI POSTs a CSV (against a live
mapping template, for one store); dis-ui-server saves it to GCS and publishes `csv.received`.
Everything past the publish is the already-built `csv-ingest-worker` (9b). This doc is
goal-level: the flow, constraints, and decisions below are fixed; the API design
(request/response, multipart contract, status/error codes) is CC's to design in plan mode and
show for review before code.

This slice supersedes the inherited signed-URL design. The original Phase 1 (D36) handed back
a short-lived signed PUT URL and the browser uploaded directly to GCS, leaving an unresolved
"how does the server learn the PUT completed" fork (D54 open mechanic). With a 10 MB ceiling
there is no large-file case for direct-to-GCS, so the bytes stream through the server in one
request and the entire upload-session / signed-URL / completion-detection complex is removed.

## Depends on

- Slice 13a (foundation, built/pushed): the FastAPI app, the auth seam
  (`get_current_identity` / `require_tenant`, the dev-stub HS256 verifier), the `dis-core`
  error to §2.3 envelope, structured logging, `dis-rls` wiring, the `/api/v1` mount, the ORM
  base.
- Slice 14b (built/pushed): the `config.source_mappings` ORM model and the reusable patterns
  this slice follows (ORM Core-style on the `rls_session` connection, never `AsyncSession`;
  tenant from token; the §2.3 envelope; the validation conventions). `template_id` validation
  here reuses 14b's model.
- Slice 4 (`dis-storage`): `build_object_path` (the canonical GCS path, UUID tenant segment,
  D53) and GCS object write. Slice 6 (`dis-audit`): fire-and-forget audit. Slice 3
  (`dis-core`): `new_uuid7`, `trace_id` mint, timestamps, structured logging.
- Slice 9b (`csv-ingest-worker`, built/pushed) by CONTRACT only: dis-ui-server publishes
  `csv.received`; the worker consumes it. This slice edits that contract (adds `template_id`)
  and the worker's pass-through; it does not change the worker's logic.
- The Identity Service is the Slice 2 fake and auth is the 13a dev-stub verifier (13b not
  built). This slice builds against the stub seam where identity/session resolution is needed,
  the same posture every slice has used; 13b swaps the seam later.
- Decisions in force: D36 (Phase 1 in dis-ui-server, not a separate receiver, the
  signed-URL specifics of which this slice supersedes), D53 (UUID tenant-segment GCS path),
  D54 (`csv.received` is the trigger; carries resolved identity so the worker re-resolves
  nothing, the completion-detection fork of which this slice removes), D52 (tier-0 structural
  CSV validation is owned by this endpoint; UUID identity + optional codes on the contracts),
  D37 (identity resolved once here, internal UUID), D5 (the worker is bronze-first
  downstream), D71 (`template_id` is carried end to end now but the consumer remains
  template-unaware until Slice 8a; the hard gate: no second-ACTIVE-template path before 8a).
- Decisions to REGISTER (operator assigns numbers at the commit gate): (1) Phase 1 CSV upload
  is synchronous, streaming the file through dis-ui-server to GCS in one request; no
  upload-session, no signed PUT URL, no completion detection (supersedes D36's signed-URL
  mechanic and closes D54's open completion fork; rationale: the 10 MB ceiling removes the
  large-file case). (2) bronze persists `template_id` (the small worker-side addition for
  replay lineage, per D71).

## Goal

After this slice, the DIS UI can upload a CSV in one request: dis-ui-server authenticates the
caller, enforces the size and structural gates, resolves identity, persists the file to GCS,
emits audit, and publishes `csv.received` carrying the resolved identity, codes, `trace_id`,
`template_id`, and the GCS pointer. The 9b worker consumes it unchanged (plus the
`template_id` pass-through). No bronze write here (the worker owns bronze); no signed URL.

### The flow (one synchronous endpoint)

1. `dis-ui` POSTs multipart: the CSV file + `template_id` + `store_code`.
2. dis-ui-server: authenticate (tenant_id + user_id from the verified token, never the body);
   enforce the 10 MB limit (see below); run the tier-0 structural gate (D52); validate
   `template_id` belongs to the token's tenant and is a usable/live template; resolve
   `store_code` to the internal `store_id` UUID; mint `trace_id`; build the canonical GCS path
   via `dis-storage` (UUID tenant segment, D53); stream the file to GCS; emit audit; publish
   `csv.received`; return.
3. `csv-ingest-worker` (already built) consumes `csv.received`, now carrying `template_id`.

### Fixed constraints and decisions

- **Tenant + user from the token only.** `tenant_id` from `require_tenant`; `user_id` from the
  token `sub`. Never the multipart body (the 14b foundation rule; a body `tenant_id` would be
  a cross-tenant write). Body carries `template_id`, `store_code`, the file.
- **One CSV = one store (v1 constraint).** `store_code` is an upload-level field resolved once
  and stamped on the event/path. A CSV carrying multiple stores' rows is out of scope (a
  future relaxation with a named trigger).
- **10 MB backend limit, enforced mid-stream.** The size cap is a security/integrity boundary
  the server enforces by rejecting as bytes cross the ceiling, NOT by reading the whole body
  then checking (a read-then-check lets an oversized POST exhaust memory first). A
  `Content-Length` early-reject is a cheap first check but is spoofable, so the streaming
  guard is the real boundary. The frontend's own limit is a UX nicety on Amit's side, not
  built here and never trusted. This is the first file-body endpoint, so the streaming-limit
  pattern is a pattern-setter.
- **Tier-0 structural validation (D52), owned by this endpoint.** File present, non-empty,
  decodes, parses as CSV, min-rows floor. Structural only, no column- or mapping-aware checks
  (those are the source-shape suite, downstream). A tier-0 failure is a clean 4xx, no GCS
  write, no publish.
- **`template_id` required on `csv.received`, carried not used (D71).** The contract gains a
  required `template_id`; dis-ui-server populates it; the worker passes it through to
  `ingress.ready`; bronze persists it. The streaming consumer remains template-unaware until
  Slice 8a. Safe today (one ACTIVE template per source); the hard gate (no second-ACTIVE path
  before 8a) is recorded under D71.
- **The worker's trust model (D54) is preserved.** dis-ui-server still resolves identity and
  puts the UUIDs + codes on the event, so the worker re-resolves nothing. Only how the bytes
  arrived changed (direct POST vs signed-URL), not what is published.

## Task

Build the endpoint and its supporting pieces in `services/dis-ui-server/`, the `template_id`
contract carry, and the worker/bronze pass-through. Confirm live shapes in plan mode; do not
assert them. Decompose in plan mode and show:

0. **Plan-mode grounding (ERROR, not skip).**
   - The live `csv.received` contract (the 9a-authored schema): the exact required/optional
     field set, and that adding a required `template_id` is the only contract change needed.
     Confirm `additionalProperties:false` forces the edit and that the only consumer is the
     9b worker.
   - The live `ingress.ready` contract and the worker's publish: confirm the `template_id`
     pass-through is additive (the worker re-emits, no logic change).
   - The bronze `bronze.data_ingress_events` shape: confirm it has no template column and
     state the minimal Alembic add + the worker write change to persist `template_id`.
   - `dis-storage` `build_object_path`: confirm the signature and that it produces the D53
     UUID-tenant-segment path; confirm the GCS write path for a streamed body.
   - The 14b `config.source_mappings` model and what "usable/live template" resolves to today
     (status ACTIVE; note the single-ACTIVE-per-source reality and the D71 gate).
   - How `store_code` resolves to `store_id` for the token's tenant (the `identity_mirror`
     read, the in-query scoping from 14b's stores repo), and that a `store_code` not belonging
     to the tenant is a clean 4xx, not a cross-tenant resolve.
   - The Identity Service session-validation seam: where the stub is, and how this endpoint
     validates the upload is from an authenticated session (against the fake/stub now, 13b
     later).

1. **API design + shapes (the deliverable for review).** The route, method, the multipart
   request (file + fields), the response body (Pydantic), status codes, and the domain-error
   to status mapping. Follow the house conventions (tenant from token; §2.3 envelope;
   snake_case codes; `/api/v1`). Show every error path: oversized (413), tier-0 structural
   failure (4xx), unknown/cross-tenant `template_id` (404), non-usable template state (409 or
   422, CC's call with reasoning), `store_code` not resolvable for the tenant (4xx), GCS write
   failure (5xx), publish failure (the ordering question below).

2. **The handler.** Auth, size guard (streaming), tier-0 gate, template + store resolution,
   `trace_id` mint, GCS path build + streamed write, audit, publish, response. Thin handler;
   the resolution/validation in a repo/service layer per the 14b pattern.

3. **Ordering and failure semantics.** State the order of GCS-write vs publish and what
   happens if the publish fails after the object is written (an orphaned GCS object with no
   event), and the idempotency story for a retried upload (the `upload_session_id` /
   `source_payload_id` lineage the worker keys its 24h idempotency on, given there is no
   session object now, CC states what fills that role, the `trace_id`, a content hash, or a
   minted id, and how a client retry is handled).

4. **The `template_id` carry ripple (in scope).** The `csv.received` contract + example, the
   dis-ui-server publish, the worker's `csv.received` parse + `ingress.ready` re-emit, the
   `ingress.ready` contract + example, and the bronze column (Alembic + the worker write).
   This is the CARRY set only. Do NOT amend the consumer's mapping lookup (that is Slice 8a,
   D71) and do NOT build any promote-to-ACTIVE path.

5. **Tests (same commit).** Tenant-from-token proven against a smuggled body `tenant_id`;
   the 10 MB limit proven by an oversized body rejected mid-stream (not after full read);
   tier-0 failures (empty, non-CSV, below min-rows) rejected with no GCS write and no publish;
   unknown/cross-tenant `template_id` and cross-tenant `store_code` rejected; a valid upload
   writes the object at the D53 path and publishes a `csv.received` carrying the resolved
   identity + codes + `trace_id` + `template_id` + GCS pointer; the worker parses `template_id`
   and persists it to bronze. Live-stack tests error (not skip) if the stack is absent.

## What this slice does NOT do

No bronze write in dis-ui-server (the worker owns bronze; this slice only adds the bronze
`template_id` column the worker writes). No signed PUT URL, no upload-session object, no
completion detection (all removed). No consumer mapping-lookup change and no promote/reject/
shadow path (Slice 8a / D71). No column- or mapping-aware CSV validation (downstream
source-shape suite). No edit to `services/dis-ui` (READ-ONLY; the frontend size limit and the
upload UI are Amit's). No real Identity Service or JWKS auth (13b). No DDL beyond the single
bronze `template_id` column.

## Open questions for plan mode

1. The idempotency / lineage key now that there is no upload-session object: what fills the
   `source_payload_id` role the worker keys on (CC's recommendation with reasoning).
2. GCS-write vs publish ordering and the orphaned-object failure mode (CC states the chosen
   order and the cleanup/acceptance posture).
3. The non-usable-template-state response code (409 vs 422) and what states are "usable."
4. The session-validation seam shape against the stub (what this endpoint checks now, what
   13b replaces).

## Acceptance criteria

- One synchronous `/api/v1` endpoint accepts a multipart CSV + `template_id` + `store_code`,
  with CC's reviewed-and-approved shapes.
- `tenant_id` + `user_id` come only from the token; a smuggled body `tenant_id` is ignored
  (test-proven).
- The 10 MB limit is enforced mid-stream; an oversized upload is rejected before full-body
  read (test-proven).
- The tier-0 structural gate rejects empty / non-CSV / below-min-rows with no GCS write and no
  publish.
- `template_id` is validated against the token tenant's `config.source_mappings` (via
  `rls_session`); `store_code` resolves to the tenant's `store_id`; cross-tenant values are
  rejected, not resolved.
- A valid upload writes the file to the D53 GCS path, emits audit, and publishes `csv.received`
  carrying the resolved UUID identity + codes + `trace_id` + `template_id` + GCS pointer.
- `csv.received` and `ingress.ready` carry a required `template_id`; the worker passes it
  through and persists it to bronze; the consumer is NOT amended (D71 / Slice 8a).
- `services/dis-ui` is unmodified; no DDL beyond the bronze `template_id` column.
- The two register entries (synchronous-streaming-upload supersedes signed-URL; bronze
  persists `template_id`) are recorded; D36/D54 noted superseded-in-part.
- `make check` / lint / mypy clean; tests in the same commit.
