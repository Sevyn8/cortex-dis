# Slice 15a: quarantine read endpoints (dis-ui-server)

First slice of the Slice 15 endpoint group. Two tenant-facing GET endpoints that back the
Quarantine console: a list (table plus filters) and a single-item detail. This doc is
goal-level: the route shapes, the request/response bodies, the data-source joins, the
RLS posture for the quarantine tables, and the best implementation are CC's to design in
plan mode and show for review before any code. The endpoints, their displayed fields, the
auth posture, and the constraints below are fixed. The frontend is Amit's and is consumed
read-only for shape compatibility, never edited.

## Depends on

- Slice 13a (foundation), built and pushed: the FastAPI app, the auth seam
  (`get_current_identity` / `require_tenant` / `require_ops`, dev-stub verifier), the
  `dis-core` error-to-HTTP envelope (§2.3 shape), structured logging, `dis-rls` per-tenant
  session wiring, the SQLAlchemy declarative base, and the `/api/v1` prefix (probes at root).
- Slice 14b (the API pattern), built and pushed: tenant-from-token only, bare resource/array
  responses (no success envelope), the §2.3 error envelope, declarative models executed
  Core-style on the `rls_session` connection (never `AsyncSession`), 404-throw-style lookups,
  clean snake_case error codes. 15a follows this pattern, it does not reinvent it.
- Slice 11a / D82 (the data this reads), built and pushed: the streaming consumer writes
  deterministic failures directly to the `quarantine.*` tables (chunk-level to
  `quarantined_chunks`, row-level to `quarantined_rows`), `status=NEW` only. No lifecycle
  transition (resolved/closed) exists, and no replay exists. This is the load-bearing
  constraint on the Status filter and the Resubmit action (see Scope).
- Slices 30b / 30c, D78 + D79 (the failure-audit contract): the FAILURE audit-row shape
  (`trace_id`, `tenant_id`, `data_ingress_event_id`, `mapping_version_id`, the `FailureCode`
  enum, `failure_message`, `duration_ms`, `row_offset`, `event_data.check`) and the closed
  `FailureCode` vocabulary. A held row joins its audit story by `trace_id` (the spine), its
  chunk by `data_ingress_event_id`, its template by `mapping_version_id`. The Error, Stage,
  and Context fields on both endpoints derive from this contract.
- `libs/dis-storage` (D53 canonical GCS path) and bronze (`bronze.data_ingress_events`,
  GCS pointer to the raw chunk): the source of the ORIGINAL PAYLOAD shown on the detail
  endpoint, IF the row payload is reconstructed from the chunk rather than stored on the
  quarantine row (a Task 0 derivation).
- Decisions this slice honours: D76 (DIS has no platform see-all, single-GUC tenant
  isolation, this slice is tenant-facing only and does not trigger the first ops-read
  slice), D78 / D79 (the failure shape and vocabulary, read-only here), D82 (the
  quarantine data, `status=NEW` only), D26 (BFF), hard rule 2 (PII tokenized at receivers,
  bearing on what the displayed payload can contain), hard rule 9 (GCS only via
  `dis-storage`).

## Decision to REGISTER (operator assigns the number at the commit gate)

The tenant-isolation mechanism for the quarantine read depends on the live RLS posture of
the `quarantine.*` tables, derived in Task 0. Two cases, and the plan states which holds:

- If `quarantine.*` is RLS ON (single-GUC `app.tenant_id`, as canonical and `config` now
  are), the read rides the policy through `rls_session` and there is no new weak link.
- If `quarantine.*` is RLS OFF, the read scopes by an in-query `tenant_id` predicate, which
  is a known weak link with no database backstop (the D70 pattern), and is registered with
  a revisit trigger: bring `quarantine.*` under RLS before it carries more tenant-facing
  read surface.

## Goal

After this slice, dis-ui-server serves two endpoints under `/api/v1` that let a tenant
operator browse their held rows and inspect one in full. Both are tenant-scoped: `tenant_id`
comes from the verified token only, never a path, query, or unverified header. Reads run
through the posture Task 0 establishes (RLS policy via `rls_session`, or in-query scoping if
the tables are RLS OFF). Response shapes are clean and simple to consume, designed by CC and
shown in plan mode, not reverse-engineered from the current frontend.

### The two endpoints

a. **List, proposed `GET /api/v1/quarantine`** : the tenant's held rows, newest first, for
   the table and its filters. Per row the UI shows: a timestamp (Time), a source display
   name (Source), the failure message (Error), the failure stage (Stage), and the
   `trace_id` (Trace, with a copy affordance the server need not support beyond returning
   the value). The endpoint also returns the open count the header shows ("3 open").

   Four filters, all server-side, all combinable:
   - **Source**: all, or one source. The vocabulary is the tenant's distinct held sources.
   - **Error type**: all, or one of the stage/error categories the rows carry (the screens
     show source-shape, canonical-shape, fk, normalization). This vocabulary must be the
     same source as the Stage column the rows display (see Principles, one canonical truth).
   - **Status**: the screens offer all / open / resolved. Only `NEW` (open) has a producing
     path today (D82). The slice states the honest behaviour: open maps to `NEW`; resolved
     returns nothing because no row can be resolved yet; the value is forward-compatible and
     lights up when the lifecycle slice lands. The slice does not fabricate resolved data.
   - **Time**: all, or a trailing window (the screens show last 24 hours / 7 days / 30 days),
     applied to the row timestamp.

   The open count is a count of open (`NEW`) rows for the tenant, independent of the active
   filters (it is the header badge, not the filtered total).

b. **Detail, proposed `GET /api/v1/quarantine/{id}`** : one held item in full, for the Row
   detail panel. The addressing key (a `quarantined_rows` / `quarantined_chunks` id versus a
   `trace_id`) is a Task 0 derivation, since a trace can hold many failed rows, so the row
   is the likely grain. The panel shows: a header line (Trace, Source, Time, and a version
   token the screen renders as "v1"), the Error, the Stage, a fuller Context string, the
   ORIGINAL PAYLOAD (the raw row), and Chain depth.

   - **Version token ("v1")**: derives from `mapping_version_id` where it exists (post-lookup
     failures carry it, D78). Pre-lookup failures (source-shape) carry none; the slice states
     how the header renders in that case rather than inventing a version.
   - **Context**: the fuller failure description (the screens show "canonical-shape
     validation: column price failed numeric cast"). Composed from the D78 failure fields
     (`failure_message`, `event_data.check`, the stage), not a second stored string. The plan
     shows the composition.
   - **ORIGINAL PAYLOAD**: the raw row as ingested. Whether this is stored on the quarantine
     row or reconstructed from the bronze chunk in GCS (via `dis-storage`, located by
     `row_offset`) is a Task 0 derivation, and it carries two load-bearing constraints: the
     GCS read must stay tenant-isolated (the D53 path tenant segment), and the displayed
     payload must not expose raw PII (hard rule 2: receivers tokenize before persistence, so
     confirm what the stored chunk actually contains before rendering it to the tenant UI).
   - **Chain depth**: the replay-lineage depth (0 for an original, not a replay). Replay and
     the `parent_trace_id` chain are Slice 12 (TODO), so this is 0 today; the slice states
     it surfaces the real value when lineage exists and is 0 until then, not omitted.

### Principles the plan must honor (CC proposes how; these are constraints, not solutions)

- **One canonical truth for the stage/error taxonomy.** The Error-type filter vocabulary and
  the Stage value the rows display must come from one source (the D79 `FailureCode` enum or
  the failure stage, reconciled in Task 0), not two independently derived lists that can
  drift. The list endpoint and the detail endpoint must agree on it.
- **Tenant from token only.** For both endpoints, and for the open count, the `tenant_id` is
  sourced solely from the verified token, never a body, query, or unverified header. This is
  test-enforced, and especially load-bearing if Task 0 finds `quarantine.*` RLS OFF (no
  database backstop).
- **Honest Status semantics.** The Status filter must reflect what the data can express
  (`NEW` only, D82). Resolved is exposed as a forward-compatible value with no rows, not
  faked, and not silently dropped from the filter.
- **GCS and PII discipline on the payload.** The ORIGINAL PAYLOAD path obeys hard rule 9
  (GCS only via `dis-storage`, never an improvised path) and hard rule 2 (no raw PII
  surfaced). If the stored chunk could contain untokenized PII, that is a surfaced product
  question, not a silent render.
- **Show how the response is assembled from more than one table.** No displayed field is
  self-evidently sourced: the response is composed across `quarantine.*`, the FAILURE audit
  rows (joined by `trace_id` / `data_ingress_event_id`, D78), and possibly bronze plus GCS
  for the payload. The plan must show a field-to-source map (every list-row field and every
  detail field, which table or column it comes from, and the join keys that connect them),
  not just "it joins the spine". A field the plan cannot source from a real column is a
  surfaced gap, not a placeholder.
- **Surface gaps, do not paper over them.** The plan's job is to make the missing and the
  unresolved visible, not to quietly resolve or omit them. CC reports an explicit "Gaps and
  open decisions" list as a named plan-mode output (see Task), covering at minimum: the
  Status open/resolved producing-path gap (no row can be resolved today, D82), the
  multi-table response composition above, the Source-display derivability question, and the
  original-payload source and PII posture. Anything Task 0 cannot resolve from the live DB or
  an existing decision goes on this list with a one-line reason, routed to the operator or to
  a trigger slice. A gap that is invisible because the plan silently chose a default is the
  failure mode this slice is guarding against.

### What this slice does NOT do

No Resubmit and no replay: Resubmit is a write/action (replay), built on Slice 12 replay
tooling (TODO) and exposed later as its own dis-ui-server endpoint, not here. No quarantine
lifecycle transition (resolve / close / status change), since no producing path exists
(D82); the resolved Status value is read-only-empty until the lifecycle slice lands. No
ops / cross-tenant quarantine console (the platform see-all, D76, first ops-read slice:
a `dis-rls` `user_type` variant plus a policy migration on every tenant table). No
edit-and-replay in-line correction (architecture: post-v1.0). No 11b topic + drainer and
no broad failure classification. No DDL (11a was zero-schema-change; 15a writes none). No
edit to `services/dis-ui` (READ-ONLY, absolute; all frontend change is Amit's).

## Task

Build the two handlers under `services/dis-ui-server/`, the read-side access to
`quarantine.*` and the joined audit FAILURE rows, the source-display and stage/error
mapping, the original-payload retrieval, and the Pydantic request/response models, on the
13a base and the 14b pattern. Confirm the live shapes in plan mode; do not assert them.
The plan must end with an explicit **Gaps and open decisions** list (per the surface-gaps
principle): every item Task 0 could not resolve from the live DB or an existing decision,
each with a one-line reason and a route (operator call, or a named trigger slice). The
Status open/resolved gap and the multi-table response composition appear here unless Task 0
fully closes them. A plan that resolves these silently, instead of listing them, is
incomplete. Decompose in plan mode and show:

0. **Plan-mode grounding (ERROR, not skip).**
   - **`quarantine.*` RLS posture and shape.** Introspect the live `quarantined_chunks` and
     `quarantined_rows`: their columns, their RLS posture (ON single-GUC, or OFF), the
     status vocabulary actually present (expect `NEW` only, D82), and whether a row payload
     is stored on the row or only a pointer to the bronze chunk. This decides the read
     mechanism (`rls_session` vs in-query scoping) and the registered weak link above.
   - **The failure-audit join (D78 / D79).** Confirm how a `quarantine.*` row reaches its
     Error, Stage, and Context: from columns on the quarantine row itself, from the joined
     FAILURE audit row by `trace_id` / `data_ingress_event_id`, or both. Reconcile the
     stage/error taxonomy to one source so the filter vocabulary and the displayed Stage
     cannot drift.
   - **Source display vocabulary.** Derive where the Source display name comes from
     ("Manual CSV Upload", "Shopify POS"): the `dis_channel` enum, the
     `config.source_mappings` template the upload used, or a source identity. Note that no
     source registry exists (14b). If the display name is not derivable from existing data,
     that is a surfaced product question, not an invented label.
   - **Original payload source.** Confirm whether the per-row payload is stored on
     `quarantined_rows` or reconstructed from `bronze.data_ingress_events` plus the GCS chunk
     (via `dis-storage`, located by `row_offset`). State the read path, the tenant-isolation
     of the GCS access (D53 tenant segment), and whether the stored content can contain raw
     PII (hard rule 2).
   - **Addressing key for detail.** Confirm whether detail is keyed by a quarantine row id or
     by `trace_id`, given a trace can hold many failed rows. State the grain.
   - **Version token and chain depth.** Confirm `mapping_version_id` availability per failure
     stage (the "v1" header), and that `parent_trace_id` lineage is absent today (Chain depth
     0 until Slice 12).
   - **Frontend read (no edit).** Read `services/dis-ui` to see how these two screens consume
     the data, enough to design a compatible, simple shape; do not match it field-for-field
     and do not edit it.

1. **API design + shapes (the deliverable for review).** Propose both routes, methods, the
   query parameters for the four filters plus the open count, the response bodies (Pydantic
   models) for the list row, the list envelope-free array plus count, and the detail object,
   the status codes, and the error mapping. Follow the 14b house conventions: tenant from
   token only; bare responses; the §2.3 error envelope; `/api/v1` prefix; 404-throw-style on
   an unknown detail id; clean snake_case error codes. Normalize any route-naming
   inconsistency and show it.

2. **List handler.** The filtered, ordered read with the four filters and the open count,
   through the Task 0 read mechanism. The Status filter behaves per the honest semantics
   (open maps to `NEW`, resolved returns empty). The Error-type and Source vocabularies come
   from the reconciled single source.

3. **Detail handler.** The single-item read by the Task 0 grain, composing Error, Stage,
   Context, the version token, Chain depth, and the ORIGINAL PAYLOAD (via the Task 0 payload
   path, tenant-isolated, PII-safe). A not-found id returns a clean 404, not a 500.

4. **Tests (same commit, code-quality rule 3).**
   - Tenant isolation on both endpoints: a token for tenant A cannot list or read tenant B's
     held rows, and `tenant_id` is sourced only from the token (the foundation rule made
     executable, and the sole backstop if `quarantine.*` is RLS OFF).
   - Each filter, alone and combined: Source, Error type, Time window, and Status, including
     the proof that resolved returns empty (no producing path) while open returns `NEW` rows.
   - The open count is the count of open rows for the tenant and is independent of the active
     filters.
   - Detail: a valid id returns the full object with Error / Stage / Context composed from
     the failure contract, the version token present only where `mapping_version_id` exists,
     Chain depth 0, and the ORIGINAL PAYLOAD retrieved tenant-isolated; an unknown id returns
     404; a tenant cannot read another tenant's item by id.

## Open questions for plan mode

1. `quarantine.*` RLS posture (ON single-GUC vs OFF), deciding the read mechanism and the
   registered weak link (Task 0).
2. Error / Stage / Context source: quarantine-row columns, the joined FAILURE audit row, or
   both, reconciled to one taxonomy source (Task 0).
3. Source display vocabulary and where it derives from; any non-derivable label is a surfaced
   product question (Task 0).
4. Original-payload source (stored on the row vs reconstructed from bronze + GCS), its
   tenant-isolation, and its PII posture (Task 0).
5. Detail addressing grain (row id vs `trace_id`) and the route-naming normalization.
6. The version-token rendering for pre-lookup failures that carry no `mapping_version_id`.

## Acceptance criteria

- Both endpoints serve under `/api/v1` with CC's reviewed-and-approved shapes, following the
  14b pattern (tenant from token, bare responses, §2.3 envelope, Core-style `rls_session`
  execution, 404-throw lookups).
- The list returns the tenant's held rows only, newest first, with the four filters working
  alone and combined, and the open count as the count of open (`NEW`) rows independent of the
  filters; a token for tenant A cannot see tenant B's rows (test-proven).
- The Status filter behaves honestly: open returns `NEW` rows; resolved returns empty with no
  fabricated data; the value remains in the filter as forward-compatible.
- The detail returns one item by the chosen grain with Error, Stage, and Context composed
  from the D78 / D79 contract, the version token present only where `mapping_version_id`
  exists, Chain depth 0, and the ORIGINAL PAYLOAD retrieved tenant-isolated (D53) and
  PII-safe (hard rule 2); an unknown id returns 404; cross-tenant read by id returns
  not-found.
- The Error-type filter vocabulary and the displayed Stage come from one reconciled source,
  proven not to drift.
- All `quarantine.*` access runs through the Task 0 read mechanism; if RLS OFF, the in-query
  scoping weak link is registered with its revisit trigger (the D70 pattern).
- GCS access for the payload goes only through `dis-storage` (hard rule 9); no improvised
  path.
- `services/dis-ui` is unmodified.
- No DDL; no quarantine write of any kind (read-only slice).
- `make check` / lint / mypy clean; tests ship in the same commit (code-quality rule 3).
