# Mapping Template Create/Promote: locked decisions record

Companion to `mapping-template-create-promote-brief.md` (Sanjeev's shared brief).
This record captures what the operator (Amit) has LOCKED for the create/promote
build, and what is still PROVISIONAL pending alignment with Sanjeev, so the
choices are documented rather than tribal. It answers the four open product
decisions in the brief's Section 3.

Status: operator-locked where marked; provisional items must be reconciled with
Sanjeev before the corresponding server endpoints are built.

---

## Decisions (the brief Section 3 open questions)

### (a) Go-live behavior: CREATE-AS-ACTIVE. REVERSED (was CREATE-AS-DRAFT; see D88).

Go-live calls the real `createMappingTemplate()` and the result is LIVE in one
step: `POST /api/v1/mapping-templates` writes the v1 version `status='ACTIVE'`
(+`activated_at`), and the response carries `active_version=1`. There is no
draft -> activate ceremony. The frontend assembles the full
`{version, rename, normalize, cast, derive}` document from the wizard state and
POSTs it. (Previously this was CREATE-AS-DRAFT with a separate activate step; that
is reversed by D88, recorded in `docs/decisions.md`.) Edit (PATCH) still writes a
DRAFT (the D17 lifecycle for changes, unchanged). Safe without supersede because
create mints a fresh `template_id` (the `uq_csm_active_per_source` partial unique
cannot collide) and without `mapping.changed` because the consumer reads ACTIVE
fresh per chunk (no cache). The D17 STAGED shadow rollout is bypassed for the
create path (accepted for beta).

### (d) Go-live success semantics: CREATED AND LIVE. REVERSED (follows from (a); see D88).

The Go-live success copy says "Created and live" (was "Created (draft)"), never
"staged". The template is active immediately, so the UI states plainly that new
files for the source are now processed through this mapping. No "(draft)" copy and
no Activate button remain in the go-live step.

### (b) Promotion granularity: ONE-STEP DRAFT to ACTIVE (STAGED dropped). PROVISIONAL, pending-Sanjeev.

The promote UI is built to a ONE-STEP lifecycle: a single operator action takes a
template DRAFT to ACTIVE directly. STAGED has been DROPPED from the create/promote
flow: there is no Stage step, no intermediate STAGED state, and no STAGED copy in
this flow. (The wire types still carry `staged_version`/`'staged'` as a
backend-contract mirror, and the separate D17 staged-rollout screens are
unaffected; only the create/promote flow is one-step.)

This SIMPLIFIES the contract Sanjeev agreed to build: the server needs ONE
`/activate` endpoint and NO `/stage` endpoint. This must be FLAGGED to Sanjeev so
the UI shape and the endpoint shape agree before the server promotion endpoint is
built. It is NOT yet confirmed with him.

### (c) What triggers ACTIVE: direct operator action ("Activate"). PROVISIONAL, pending-Sanjeev.

The provisional trigger for ACTIVE is a direct operator action. This is NOT yet
confirmed and touches D17 (staged rollout: validate in shadow against live traffic
before promoting to active). The final trigger may become shadow-gated rather than
a direct action; it must be aligned with Sanjeev. The UI is built to the direct
action provisionally and must adapt if the gate model is chosen.

---

## Build posture

The full create to activate UI is built now (one-step DRAFT to ACTIVE),
real-endpoint-wired (not deferred behind a stub-only seam). Mode behavior:

- Fixture mode (local and demo) synthesizes the transition so the whole flow is
  walkable without a backend (a clearly-marked demo activation).
- Real mode, create-as-DRAFT: REAL. The `POST /api/v1/mapping-templates` endpoint
  exists, is validated, RLS-scoped, and tested; Go-live calls it for real.
- Real mode, activate: the single `/activate` endpoint does NOT exist yet.
  Real-mode activate surfaces an honest "activation endpoint not yet available"
  state and the lifecycle stays DRAFT.

Honesty guard (hard rule for this build): real-mode promotion must NEVER fake a
successful ACTIVE. ACTIVE gates real data ingestion (CSV upload binds to the
ACTIVE version), so a fabricated activation would imply the pipeline is live when
it is not. A faked ACTIVE is forbidden; real mode shows the not-yet-available
state instead.

---

## Cross-team conflict: where the AI call lives (RESOLVED)

The brief's Section 2 states that AI assistance is purely a frontend concern and
that `dis-ui-server` does not proxy, relay, or call AI on the frontend's behalf
(AI never appears on the wire; only the finished `{source_id, template_name,
mapping_rules}` document crosses to the backend).

What was built is the opposite: the `dis-ui-server` BFF endpoint
`POST /api/v1/mapping-suggestions` calls the model server-side, chosen for
credential safety (no model credential can live in a browser bundle) and for the
server-side catalog guardrail (the model cannot invent a non-catalog target). Note
the auth model evolved past the original framing: it is NOT an API key but Vertex
AI with GCP-native credentials and gemini-dis impersonation, so there is no key to
hold (the `dis-gemini-api-key` secret was removed).

- Brief Section 2 (original): AI is frontend-only; the backend never calls AI.
- Shipped reality: `POST /api/v1/mapping-suggestions` calls Vertex AI server-side
  (GCP-native auth, gemini-dis impersonation, no key), per
  `docs/slices/llm-mapping-suggestion-contract.md`.

RESOLVED: Sanjeev has agreed to update brief Section 2 to match the shipped
server-side design. The frontend's suggestion source is the BFF endpoint.

## gemini-dis privilege acceptance (project Owner; ACCEPTED 2026-06-07)

`gemini-dis` currently holds `roles/owner` on the project, and `dis-ui-server`
can impersonate `gemini-dis` (`roles/iam.serviceAccountTokenCreator` on the
gemini-dis service account) for the Vertex calls. The mapping-suggestions endpoint
therefore has a transitive path to project Owner.

ACCEPTED by Amit as of 2026-06-07, as a conscious interim posture (pre-deploy, no
real Vertex traffic flows yet). REVISIT TRIGGER: before the AI endpoint serves real
traffic, i.e. before the Cloud Run Vertex env wires `GEMINI_IMPERSONATE_SA` and real
Vertex calls flow. The minimal fix when revisited: drop gemini-dis to
`roles/aiplatform.user` only (it does not need Owner for its actual job; the
aiplatform.user grant is already codified in `terraform/envs/staging`).
