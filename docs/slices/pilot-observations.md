# Pilot observations

One entry per slice. Lessons worth keeping, not incident logs. Keep each entry
crisp; this doc is context for CHAT and must not bloat.

---

## Slice 1: Bootstrap Alembic migration

Outcome: 8/8 criteria; two commits; three plan cycles.

Lessons:
- Safety is not surfaced unprompted. Any slice that writes/drops/touches Postgres gets a forced target-safety pass (which DB/port, what the destructive path does). Now item 1 of the plan contract.
- Summaries get read as facts. Load-bearing claims carry inline evidence (file+line or introspected row), not a summary.

Carried forward:
- Heavier review is not the leverage; the safety pass and evidence-gate are. Read-only slices need less.

---

## Slice 3: shared-library primitives + canonical models

Outcome: criteria passed; scoped commits; register clean. First pure-library slice (no writes).

Lessons:
- Green is not completeness. A passing test proves the artifact agrees with its test; if both came from one source pass, an omission passes silently. Verify "mirrors X" claims against X independently (fresh pull, exact match both directions), not the artifact's own test.
- Force an inventory at completion (files, deps, tests + what each asserts). Assess coverage, not pass/fail. Now a gate.
- Log register gaps before commit, own identifiers, same pass. Decisions in force without an entry are debt the next slice inherits. Now a gate.
- CC pushing back on review feedback (re-verifying rather than complying) is correct, not friction. Feedback is input to check, not a verdict.
- Decision-vs-schema drift recurs; expect it, don't treat as one-off.

Carried forward:
- Pure-library/read-only slices: light safety pass, leverage shifts to completeness verification and register hygiene.

---

## Slice 4: Data plane safety (dis-rls, dis-pii, dis-storage)

Outcome: 8/8 criteria. First autonomous run: CC under auto edit approval, no
operator diff-watching, gated by a completion-report account plus an adversarial
self-validation pass.

Lessons:
- Autonomous + adversarial self-check can replace diff-watching, but only if the
  check pulls from a source independent of the implementation. Forced re-derivation
  (re-introspect the live DB, re-run gates the check did not author) plus an honest
  "what a test enforces vs what is review-only" split caught issues diff review did
  not.
- A self-check at the same vantage as the work shares its blind spot. Make it
  re-derive from live sources and force the "what could a passing test still miss"
  question.
- Skip-masking: a load-bearing test that skips when its dependency is absent
  reports green without running. Load-bearing proofs ERROR at setup, never skip, and
  never fall back to a guessed default (a silent fallback is the same disease).
- Dropping diff-watching leaves unenforced invariants as residue: properties that
  hold structurally but fail loud on nothing. The fix is executable invariants
  (lint + per-rule tests), not more watching.

Carried forward:
- Guardrails work owed: executable invariants (import-linter contracts + per-rule
  tests + a negative-space scope check) and CI that errors on an absent mandatory
  proof rather than skipping it.

---

## Slice 5: Pipeline mechanics (dis-mapping, dis-validation)

Outcome: 8/8 criteria; seven scoped commits; two plan cycles. First slice where the
logic itself is the deliverable (mapping engine + validation suites), not plumbing.

Lessons:
- Introspection truncated at display width mis-classifies silently. Reading column
  comments through a tool that truncates produces wrong classifications with the
  right totals, which the count test cannot catch. Pull full untruncated comments;
  "right total, wrong bucket" is a named self-validation hunt item for any
  classification work.
- The completion report is an account to check, not trust. Its stated counts were
  wrong and were corrected only by independent re-derivation in self-validation, not
  by the inventory pass.
- Independent expected values catch silent-wrong-value bugs. A plausible-but-wrong
  parse passes any test whose expected value was copied from the impl; it fails a
  test whose expected value was hand-derived from the rule. Expected-value-from-an-
  independent-source is what catches the silent-wrong class.
- Mechanism-not-policy is the frame for a pure lib. Business rules stay handed-in
  data or model-derived, never literals in lib code. A default is allowed only when
  it can fail louder, never admit wrong data; the test is "can it pass bad data,"
  not "is it a default."
- Pin an invariant a downstream decision rests on, even when it holds by
  construction. A property that is true today by how the code is shaped still needs
  a test, or a future change breaks it silently and the dependent decision inherits
  the break.

Carried forward:
- Pinned-dependency bug pattern: contain the workaround, and make the canary assert
  the upstream bug STILL exists (not a version-string check), so the workaround is
  forced out when its cause changes.

---

## Slice 7: Mirror Sync Consumer (DB-pull mode)

Outcome: 9/9 criteria; five scoped commits; one plan cycle after a research gate.
First service slice; first to read a second Postgres (Customer Master) and write a
replica into DIS.

Lessons:
- Uncollected is the new skipped. A whole service test suite was invisible to the
  runner (testpaths excluded its dir); CI would have gone green running none of it.
  Self-validation caught it, the build pass did not. The runner must fail when a dir
  is uncollected, not pass silently.
- Research before the doc when scope hinges on unknown live facts, so the doc is
  accurate rather than corrected later.
- Docs name mechanisms the schema lacks; found by introspection, not by reading the
  docs. Recurs across slices.
- A frozen contract does not fit every consumer. Log-only plus a registered gap
  beats shoehorning a new case into an existing vocabulary.
- Two-instance safety = positive assertion on each side: each connection asserts the
  DB it IS on, the read runs first, a mix-up exits before any write.

Carried forward:
- Positive-target-assertion is the pattern for any later service reading an external
  Postgres and writing DIS.
- A reusable external-Postgres test harness, once built, is reused by the next
  external-reading slice, not rebuilt.
- CI-fails-on-uncollected folds into the owed guardrails work.

---

## Slice 9a: Identity correction (UUID load-bearing, codes readable)

Outcome: 8/8 criteria; nine scoped commits; two plan cycles. First cross-cutting
correction slice (not a vertical feature); first live read of Customer Master in
plan mode.

Lessons:
- The register can carry a wrong fact. D55 recorded a source column NOT NULL; live
  re-introspection showed it nullable. Verify-against-live applies to recorded
  decisions, not only code; re-introspect a shape a slice depends on rather than
  citing the D-number.
- An undocumented access reads as a prohibition. CC inferred "Customer Master is
  off-limits" because CLAUDE.md never stated the granted read access. Document
  granted accesses positively, with their traps (here the RLS-GUC zero-rows-silently
  gotcha), or the agent works around a capability it has.
- A format/lint diff that resists explanation is usually tooling-version skew, not a
  real change. A phantom "extra reformatted file" was the pre-commit ruff pin
  disagreeing with the workspace ruff; it vanished at the commit gate. Check the pin
  before treating a format diff as a finding.
- The report/self-validate/gate separation is load-bearing. CC revised its own claim
  about that phantom file three times across the three stages, each catching what the
  prior asserted. A single "looks done" pass would have shipped a confident-wrong
  claim.
- Cross-cutting shape changes resist per-file commit splits. A field rename fanned
  across ~10 files and the schema had to land with the fake that emits it (mutually
  red apart). The atomic commit is the honest unit; state the deviation from a
  proposed split with its reason.
- Additive migrations have two paths, and the fresh one is unexercised. The
  existing-DB delta runs in every test; the fresh-bootstrap path (manifest creates
  the column, the migration no-ops) never had. Rehearse it on a scratch DB, the more
  so before a cloud-first provisioning where it is the only path.

Carried forward:
- Provenance-labelled completion reports (contract / live / hand / same-pass) are the
  standard; self-validation re-derives the same-pass pairs from an independent anchor
  or states the risk is unanchorable and bounded.
- Tooling debt owed (parked): wire mypy --strict as a gate and clear the backlog
  across all code except dis-ui; align the pre-commit ruff pin with the workspace ruff
  so files stop flip-flopping on format.

---

## Slice 9b: csv-ingest-worker (CSV upload Phase 2)

Outcome: 9/9 criteria; eight scoped commits; one plan cycle after the four-question
round. First standalone event-triggered worker; first consumer of csv.received and
producer of ingress.ready.

Lessons:
- Bundling the inventory and the adversarial pass hides findings. Run standalone against
  the proper inventory, step 8 surfaced a contract gap (D60: tenant_id described as the
  Pub/Sub ordering key, no producer sets one) the bundled pass missed. Keep 7 and 8
  separate.
- Probe the real dependency's edge before authoring. The DuckDB canary found an empty
  file does not raise (it fabricates a headerless column0), so a zero-byte upload would
  have passed preflight. Boundary-probing the live dependency caught a latent bug no
  fixture-shaped test would; the fix is a guard plus a canary pinning the boundary, not a
  version check.
- Attack your own proof's escape hatch. The behavioural no-mint test (patch new_trace_id
  to explode) misses a module-level import alias; an AST test that the name is referenced
  nowhere closes it. Behavioural and structural proofs cover each other's blind spots; a
  load-bearing invariant gets both.
- A registered limitation needs its failure mode proven benign, not just named. D58's
  single-worker dedup is safe only because the worst case under a race was shown to be
  double-write or duplicate-publish (tolerable, D33 dedups at read), not corruption or a
  lost row, by proving no read-modify-write exists. Naming a limit is half the work;
  bounding its blast radius is the other half.
- A frozen contract can promise behaviour no producer provides. The ordering-key
  description (D60) was found only by reading the contract against the implementation; no
  functional test catches a field nobody sets. When a contract describes a mechanism, a
  test asserts the producer implements it, or the description is struck.

Carried forward:
- Trust-boundary invariants (the worker reads identity and trace_id off the event, never
  resolves or mints) are enforced with paired behavioural + AST proofs and a
  no-second-source check (the parsed path is dead after the consistency cross-check; only
  the event's identity reaches the RLS scope and the row).
- When a contract describes a transport mechanism (ordering key, partition routing), pin
  producer conformance to it or record the gap with a strike-or-implement trigger for the
  first dependent consumer (D60 -> Slice 10).

---

## Slice 10: Streaming consumer (happy path)

Outcome: criteria passed; three gates (two prerequisite migrations, M-D38/D64 and
M-HOTKEY, then the service), nine commits; the most plan cycles of any slice. First
consumer of ingress.ready and first canonical writer; first slice to spawn its own
prerequisite migrations mid-flight and to retract a shipped decision (D58) during the
build.

Lessons:
- Object-exists is not operation-works. The NULLS-NOT-DISTINCT key "usable as ON
  CONFLICT arbiter" (it is not, with NULL segments on PG15) and the single-statement
  upsert (NOT NULL validates the candidate before arbitration) were both plan-time
  claims falsified only at execute. Prove engine behaviour by running the statement,
  seeded scratch DB if live is empty, not by introspecting that the object exists.
- Repeated engine rejection on a new rule each time is a structure smell, not a syntax
  one. Create-a-row and update-a-row forced through one atomic statement broke twice
  (arbitration, then candidate validation); splitting them (completeness-gated
  two-path) dissolved it. Split the operation, don't hunt a cleverer statement.
- An assumption nothing enforces at runtime is debt, not a posture. D58 single-instance
  was named but unowned (no lock, no deploy control); autoscaling made it false, and no
  pass caught it, the operator's question did. Operator world-knowledge (deploy posture,
  onboarding friction) is a review input the loop cannot generate from the code.
- A design that echoes a rejected one must prove the difference structurally. The
  incomplete-projection path looks like the retired read-modify-write but never inserts;
  self-validation proved no INSERT token exists, not "this version is safe". Echoing an
  unsafe design shifts the burden to a structural proof of the distinguishing property.
- A reframe is checked against the live config, not just in principle. Completeness-
  gating (revised D63) removed the onboarding-order friction in principle, but no
  production mapping classifies complete, so the friction is not relieved yet.
  "Mechanism built, enablement not" is an honest registered end-state, not a gloss.
- "Conservative, therefore safe" is a one-direction claim; check every path. The
  completeness classifier can only under-claim into a harmless quarantine, never
  over-claim into a bad insert, but that covered only the CREATE path; a wrong
  UPDATE-path pair mis-writes silently. A safe-failure-direction argument is incomplete
  until it covers every path the misclassification reaches.

Carried forward:
- Engine-behaviour claims join derive-from-live: proven by executing the statement
  (seeded scratch DB + EXPLAIN/arbiter output when live is empty), never by object
  existence.
- Retracting a shipped assumption is a mini-arc with blast radius beyond the slice: D58
  reopened, M-HOTKEY spawned, a sibling gap exposed in another service (worker bronze
  dedup, D66). Surface posture questions (does this autoscale) before building on the
  unexamined assumption.
- After an interrupted or reversed build, sweep for orphans of the abandoned approach at
  the statement level (one INSERT in the sink, only in the complete path), not by
  identifier count alone.

---

## Slice 13a: dis-ui-server foundation

Outcome: 8/8 criteria; four commits; one plan cycle after a revision round. First BFF
service; first greenfield FastAPI service; first Dockerfile in the repo; foundation only
(no UI data endpoints, dev-stub auth, identity-service real deferred to 13b).

Lessons:
- A 503-or-error assertion that cannot name WHY it failed conflates failure modes. The
  /readyz isolation test asserted only 503; it could not tell the RLS posture-guard raise
  apart from a dead DB or a missing grant. Assert the specific cause (the guard's error
  reaches the 503 path), with the other causes ruled out by live introspection (same host,
  role holds the grant), or the test passes on the wrong failure.
- Prove a security invariant structurally when the tree allows it. An exhaustive grep
  showing exactly one request-input read in src/ (the Authorization header to the verifier)
  proves tenant_id CANNOT come from body/query/header, stronger than a test that three
  injected inputs were ignored. "Cannot reach the value" beats "was ignored here."
- The untested exception path is the unhandled one. Envelope tests covered mapped leaves
  and unmapped DisError but not a plain non-DisError bug, the likeliest leak. Pin what a
  raw exception (not a domain error) sends over the wire: no message, no type, no traceback.
- Stub-seam scope is what the consumer needs, not the real implementation's internals.
  Define the Identity the dependencies consume, not the verifier; the 13b swap is one
  module, verified by the same grep that nothing references stub-specifics.
- Task 0 ERROR-not-skip grounding earns its place pre-code: it caught doc-vs-live drift
  (DIS RLS is single-GUC app.tenant_id; the slice-doc prose described a two-GUC user_type
  posture that is Customer-Master-replica-only). Derive the live RLS mechanism; do not
  assume one schema copies another's.
- A transient mid-sequence diff is not the end state. A staged snapshot showed
  streaming_consumer being REMOVED from known-first-party (a non-interactive lift to isolate
  it into its own commit, restored immediately after); the committed tree had it present.
  Check the end state, not a snapshot, before flagging a destructive change.

Carried forward:
- caplog eviction trap: dis-core configure_logging does root.handlers = [handler], evicting
  pytest's caplog handler once an app is constructed mid-test. Capture on the named logger;
  a dis-core append-if-absent fix is owed. Bites any service test wanting caplog after app
  construction.
- Container runtime is manual-verified, not gated (first Dockerfile; app behaviour is
  test-gated, packaging is not). A container smoke test (build, curl the probes, assert,
  tear down) is owed before the packaging layer is load-bearing.
- Commit cleanliness is deprioritized in the build phase, valued in maintenance. Content
  correctness (a decision landing in the register, the right files staged, no lost entry)
  still matters; commit shape does not. Reserve gate attention for content-loss risk, not
  split discipline.
