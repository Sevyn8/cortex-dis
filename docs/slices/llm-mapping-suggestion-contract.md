# LLM mapping-suggestion endpoint: contract spec (dis-ui-server BFF)

**Status:** v3.0 contract, 2026-06-07. Grounded in the shipped code (not a proposal). SUPERSEDES v2 (which placed the call on the BFF but still described an AI-Studio API key held in a `dis-gemini-api-key` secret) and v1 (which placed the call in a separate onboarding service). The auth model is now Vertex AI with service-account impersonation, NO API key; the `dis-gemini-api-key` secret has been removed. Treat this file as the single in-repo contract for the endpoint; the prior text is intentionally not retained.

Real code this doc is grounded in:
- `services/dis-ui-server/src/dis_ui_server/api.py` (router registration)
- `services/dis-ui-server/src/dis_ui_server/handlers/mapping_suggestions.py` (the handler)
- `services/dis-ui-server/src/dis_ui_server/schemas/mapping_suggestions.py` (the wire shapes)
- `services/dis-ui-server/src/dis_ui_server/suggest/gemini_client.py` (the credential paths + catalog guardrail)
- `services/dis-ui-server/src/dis_ui_server/suggest/fallback_matcher.py` (the mechanical fallback)
- `services/dis-ui-server/src/dis_ui_server/config.py` (the env var names)

---

## 1. Framing and authority

- The mapping-suggestion call lives on **dis-ui-server** (the BFF, D26), not in the browser and not in a separate onboarding service. The dis-ui frontend continues to call only dis-ui-server (decisions D17/D26).
- The credential never reaches the browser. Auth to the model is **GCP-native** (Vertex AI Application Default Credentials, optionally impersonating a dedicated service account), so there is no API key to leak. This is the reason the call is a BFF endpoint rather than a direct browser-to-model call.
- The frontend sends a parsed column profile (produced client-side, see section 7) and renders the per-column suggestions this endpoint returns. The AI-assisted, human-approved loop is unchanged: the assistant suggests, the human confirms or corrects.

## 2. Endpoint

`POST /api/v1/mapping-suggestions` (registered in `api.py`; live in the app).

- **Auth:** identical to every other data endpoint. `Authorization: Bearer <token>`, resolved through `require_tenant` (the verified token is the sole source of `tenant_id`; no body or query carries scope). A tokenless or non-tenant call is the standard 401/403 envelope.
- **Request content type:** `application/json`.
- **Idempotent:** yes. The handler reads the in-memory field catalog and the posted profile, calls the suggester, and returns. It writes nothing to Postgres, GCS, or Pub/Sub.

### 2.1 Request body (`MappingSuggestionRequest`)

```jsonc
{
  "columns": [                          // REQUIRED, non-empty
    {
      "name": "txn_date",                // header name from the uploaded CSV
      "inferred_datatype": "datetime",   // UI vocabulary (section 5)
      "null_pct": 0.01,                  // 0..1, share of empty cells in the sampled rows
      "sample_values": ["03-12-2025", "04-12-2025"]
    }
    // ... one entry per source column
  ],
  "source_id": "manual_csv_upload",     // optional prompt context; advisory only, never trusted for scope
  "template_name": "Sales"              // optional prompt context
}
```

- Each `columns` entry is the column PROFILE only (`name`, `inferred_datatype`, `null_pct`, `sample_values`). It deliberately does NOT carry any prior suggestion (`suggested_target`/`confidence` are this endpoint's OUTPUT, never its input).
- `source_id` / `template_name` are optional prompt context. They are advisory; nothing about tenant scope or authorization is read from the body (scope comes from the token).
- The dis-ui `SampleColumn` uses `source_col` / `inferred_type` internally; it adapts to the wire names `name` / `inferred_datatype` at the client call boundary.

### 2.2 Response body (`MappingSuggestionResponse`)

```jsonc
{
  "source": "llm",            // "llm" when the model produced the suggestions, "fallback" when the
                              // mechanical matcher did (project/location unset, model error, timeout, parse failure)
  "model": "gemini-2.5-flash", // present when source == "llm"; null/absent for fallback
  "suggestions": [
    {
      "source_column": "txn_date",
      "suggested_target": "source_sale_timestamp", // a catalog KEY, or null for "do not map"
      "confidence": 0.62,                          // 0..1
      "reasoning": "Values look like day-month-year dates, so this maps to the sale timestamp.",
      "alternatives": ["transaction_id"]           // optional list of catalog KEYS (strings), LLM path only
    }
    // ... one entry per input column
  ]
}
```

- `source` is the honesty flag and is always present: the UI labels suggestions as AI-derived (`"llm"`) or mechanical (`"fallback"`) from this single field.
- `suggested_target` is either a key from the field catalog (section 3) or `null` (assistant suggests not mapping this column). It can never be a non-catalog string: the server validates model output against the catalog and nulls any invented target before responding.
- `confidence` is the assistant's self-reported confidence (LLM) or the matcher's score (fallback), 0..1. The UI renders its OK / low / very-low bands from it.
- `reasoning` is OPTIONAL (a short string, LLM path only; absent on fallback).
- `alternatives` is OPTIONAL and is a **flat list of catalog-key strings** (`list[str]`), ranked, LLM path only. It is NOT a list of objects and carries no per-alternative confidence. Any non-catalog key is dropped server-side.
- There is **no `suggested_rule`** field. The endpoint suggests a target field per column; it does not propose D49 mapping rules. The format-rule (locale) declaration stays explicit and human-owned downstream.
- The `suggestions` array has one entry per input column.

### 2.3 Errors

The standard error envelope (contract section 2.3): `{ "error": { code, message, trace_id, details } }`.

- **401 / 403** auth: missing or invalid Bearer, or a non-tenant token (the shared auth dependencies).
- **422 malformed profile:** `columns` missing or empty, or an entry missing a required profile field. Standard FastAPI validation envelope.
- **The model being unavailable is NOT an error to the caller.** Unset Vertex config, a model timeout, a transport error, or an unparseable response all DEGRADE to the mechanical fallback and return 200 with `source: "fallback"`. The suggester never raises on model trouble, so the handler has no LLM error path. Model failures are logged server-side (with `tenant_id` and `trace_id`), never raised to the browser.

## 3. The prompt and the catalog guardrail

- **Allowed target set = the field catalog.** dis-ui-server builds the canonical field catalog once at startup (`app.state.field_catalog`, the same catalog `GET /api/v1/template-mapping-fields` serves). The prompt presents these entries as the ONLY targets the model may choose, keyed by `key`.
- **Inputs to the prompt:** the catalog (key, display_name, datatype, section, description) as the closed target vocabulary, plus the posted column profiles, plus the optional `source_id` / `template_name` context.
- **Structured output, not free text.** The model is called in JSON mode (`response_mime_type="application/json"`) so the result is machine-parseable.
- **The catalog is the guardrail (mandatory, server-side).** After parsing, the server validates every `suggested_target` against the catalog key set and nulls anything not in it; it filters `alternatives` to catalog keys only. The model cannot make the UI show a field that does not exist, and this does not depend on the model behaving.
- **Cost and latency:** one model call per analyze action (per uploaded sample), never per row, run off the event loop (`anyio.to_thread`) with a bounded timeout (15s); on timeout it falls back.

## 4. Auth and credentials (Vertex AI, service-account impersonation, NO key)

dis-ui-server authenticates to the model with GCP-native credentials, not an API key. The suggester (`GeminiSuggester`) takes `(project, location, impersonate_sa)` and has three credential paths:

1. **project + location set, `impersonate_sa` set (the intended deployed posture):** the Vertex calls run as the impersonated service account (`gemini-dis`). Short-lived impersonated credentials are minted from the service's ambient ADC via `google.auth.default(...)` plus `impersonated_credentials.Credentials(target_principal=<gemini-dis>, target_scopes=[cloud-platform])`, and passed to `genai.Client(vertexai=True, project, location, credentials=...)`. dis-ui-server keeps its OWN service account for everything else (DB, GCS, Pub/Sub); ONLY the Vertex calls impersonate gemini-dis.
2. **project + location set, `impersonate_sa` unset:** the Vertex calls use the ambient ADC directly (the Cloud Run service account), `genai.Client(vertexai=True, project, location)` with no explicit credentials.
3. **project or location unset:** no Vertex call at all. The suggester returns the mechanical fallback (`source: "fallback"`).

Config env (read in `config.py`, ALL optional, none aborts startup):
- `GEMINI_VERTEX_PROJECT` and `GEMINI_VERTEX_LOCATION`: the Vertex project and location. Both unset turns the model off (fallback).
- `GEMINI_IMPERSONATE_SA`: the service-account email to impersonate for the Vertex calls (gemini-dis). Unset means use the ambient ADC directly (path 2).

Model: `gemini-2.5-flash` (default). The `google-genai` import is lazy (only the live model path needs it); `_call_model` is the single network seam tests override.

## 5. Datatype vocabulary

- `inferred_datatype` on the request uses the UI inference vocabulary (`integer`, `number`, `datetime`, `text`, `choice`), a subset of the catalog `FieldDatatype`. It is an advisory hint and need not equal the catalog datatype of the chosen target.
- `suggested_target` is always a catalog `key`; the UI looks up that key's catalog datatype to drive the mandatory format-rule prompting (decimal separator for `number`, format plus timezone for `datetime`). That behavior is unchanged by this endpoint.

## 6. The fallback (mechanical matcher, server-side)

- When `source` would be `"fallback"` (Vertex unset, model error, timeout, or unparseable output), the endpoint computes suggestions with a deterministic matcher (`fallback_matcher.match_columns`): it normalizes the column name and scores it against catalog keys and display names plus a small retail synonym set, with a bonus when the inferred datatype is compatible with the candidate field. It picks the best catalog key per column, reports the score as `confidence`, and emits no `reasoning` or `alternatives`.
- The fallback always returns a full suggestion set, so the frontend always has something to render even with no Vertex config (the expected state until the service is deployed and wired).

## 7. What the frontend does with this

- The dis-ui analyze step parses the CSV client-side (T11) and calls `POST /api/v1/mapping-suggestions` with the profile, replacing the old browser-side matcher. The browser holds no credential and runs no matcher.
- The Review Mapping screen is structurally unchanged (maps-to-field cards, auto-map and confidence bands, the format-rule gate, the sample-rows preview, the ProgressRail). Only the SOURCE of the per-column suggestion changes.
- The UI labels suggestions honestly from the `source` flag. `reasoning` and `alternatives` render only when present (LLM path); their absence is normal.

## 8. Lifecycle context

- The approved mapping creates a mapping template as a **DRAFT**, then is promoted **DRAFT to ACTIVE in one step** (the create/promote flow dropped STAGED; there is no staging step in this flow). See `docs/slices/mapping-template-create-promote-decisions.md`.
- The approval still produces the same `mapping_rules` / `SourceMapping` the pipeline expects (D49). This endpoint changes how a suggestion is produced, not what an approved mapping IS; the downstream contract is untouched.

## 9. Honesty constraints

- `reasoning` and `alternatives` are optional; the UI works with or without them and never invents them.
- Everything shown is the assistant's view, not ground truth ("the assistant suggests", "other candidates considered"). The human approves or corrects.
- `confidence` is self-reported (LLM) or a heuristic score (fallback); the UI presents it as such, never as a guarantee.
- The `source` flag must be accurate: a mechanical fallback is never labelled AI-derived.

## 10. Build and deploy status (honest)

- **BUILT and registered:** the endpoint is wired into the production app (`api.py` includes `mapping_suggestions.router`) and unit-tested against a mocked model client (the `app.state.gemini` fake-injection pattern). The mechanical fallback is fully built.
- **The real Vertex path is PENDING DEPLOY.** The Cloud Run Vertex env wiring (`GEMINI_VERTEX_PROJECT` / `GEMINI_VERTEX_LOCATION` / `GEMINI_IMPERSONATE_SA`) is authored in terraform (`terraform/envs/staging`) but NOT applied; the dis-ui-server image, the `dis-jwt-jwks` secret value, and the DB migrations are pending backend prereqs. The gemini-dis impersonation IAM (aiplatform.user on gemini-dis; token-creator for dis-ui-server) is applied in GCP and codified in terraform.
- **Until deployed and wired, the endpoint returns `source: "fallback"`** (project/location unset means the mechanical matcher). This is correct and honest; the AI is not live yet, and nothing should imply it is. The `openapi.json` in the repo is stale and does not list this route (left for Sanjeev to regenerate); the route is nonetheless live in the app.

## 11. Architecture conflict (resolved)

Sanjeev's create/promote brief Section 2 stated that AI assistance is purely a frontend concern and that dis-ui-server does not call AI on the frontend's behalf. What was built is the opposite: a server-side BFF endpoint, chosen for credential safety (no model credential can live in a browser bundle) and for the server-side catalog guardrail (the model cannot invent a target). This was an explicit, recorded divergence; Sanjeev has agreed to update brief Section 2 to match the shipped server-side design. The companion decision record (`docs/slices/mapping-template-create-promote-decisions.md`) carries the resolution and the related gemini-dis privilege acceptance.
