# DIS UI redesign: slice plan (R1 to R6)

**Status:** slice plan, internal. Derived from redesign-design-direction.md (the source of truth for look and scope) and the codebase audit (which found the code sound: 1.85% duplication, zero circular deps, one dead export; almost everything flagged is the intentional real-mode seam and design-system/contract surface). This is therefore a workflow-and-visual RESHAPE on a sound foundation, not a teardown.

**What stays (do not rebuild):** the lib/dis-ui-server fixture layer (the mutable-fixture idiom), the auth/routing hubs (AuthSnapshot, useAuth, AppRoutes), the test-render helper, and the existing design primitives (components/ui/*) which the new language extends rather than replaces. The reshape targets the visual tokens and the routes/screens layer, plus net-new screens.

**Standing discipline (every slice):** no em-dashes in repo files or commits; precise DIS / DIS UI / dis-ui-server; strict TS, no any; ESLint/Prettier clean; read docs/skills/sevyn8-workflow/SKILL.md first; plan mode before code; Claude Code does not push (operator reviews diff and pushes; pre-push hook); no Co-Authored-By; one coherent commit per slice; reconcile before each slice (fetch, ff-only, HALT on watchlist or divergence). Light and dark for every visual change.

**Vision-led but thin (every slice):** CSV is built deep and real; POS/ERP connectors (Shopify POS, Square, Other) are built thin (connection shell, coming-soon, credential shape as spec). No faked integrations or sync.

---

## R1: the new visual language (design tokens, de-risked foundation)

**Goal.** Turn the mocked direction into real DIS design tokens: the richer palette, the per-source-type color identities (CSV, Shopify POS, Square, Other), typography, card/hero treatments, badges, the progress-rail style. Prove it on ONE low-risk surface before migrating any existing screen. This is the foundation R2 to R6 sit on.

**Why first and de-risked.** A token change touches everything. Introducing it as an ADDITIVE layer proven on one new surface (not a big-bang swap of all screens) avoids wobbling the whole selector/text-based test suite at once, the same de-risk-then-adopt rhythm slice 23 used.

**Scope.**
In:
- extend the token layer (index.css @theme / CSS variables) with the new palette, the source-type color identities, and any new spacing/radius/typography tokens the direction needs, for BOTH light and dark
- add or extend the few new primitives the direction needs that do not exist yet (a hero-card treatment, a progress-rail component, a source-type color/icon mapping helper), in components/ui or components
- a single style-reference surface (a dev-only /style route or a reference screen) that renders the new tokens, the source-type identities, the hero card, the rail, the badges, and the card variants, in light and dark, as the de-risk gate
Out:
- migrating existing screens onto the new language (that happens per-screen in R2 to R6; R1 does not restyle Dashboard/Quarantine/etc.)
- removing or changing existing tokens that screens currently depend on (additive only; existing screens keep rendering unchanged until their slice migrates them)

**Hard constraints.**
1. **Additive, not a swap (FM1).** New tokens and primitives are added alongside the existing ones. Existing screens render exactly as before after R1 (their tests stay green untouched). No screen is restyled in R1.
2. **Light and dark mandatory.** Every new token and identity has a light and dark value; the style-reference proves both.
3. **The source-type identity is a single source of truth.** The CSV/Shopify POS/Square/Other color-and-icon mapping lives in ONE helper that R2, R3, R5, R6 all consume.
4. Standing discipline + light/dark.

**Acceptance criteria.**
1. The style-reference surface renders the new palette, the four source-type identities, the hero card, the progress rail, badges, and card variants in light and dark.
2. Existing screens are visually unchanged after R1 (additive); the full existing test suite stays green, untouched.
3. The source-type identity helper exists and is the single mapping (color + icon) for CSV/Shopify POS/Square/Other.
4. pnpm install / dev (200) / build / test / lint / tsc strict green; tokens validated in both modes.
5. No em-dashes; correct naming; the dead PERSONA_SUBS export removed (the one true dead line from the audit, folded in here as the first fixture-touching slice).

**Failure modes.**
- FM1: restyling existing screens in R1 (do not; additive only, migration is per-slice).
- FM2: a token only working in one mode (both required).
- FM3: duplicating the source-type color/icon mapping instead of the single helper.

**Plan-mode prompt.** "Read the sevyn8-workflow skill, this slice, redesign-design-direction.md, index.css and the @theme token layer, components/ui/* primitives. Plan to: extend the token layer with the new palette + source-type identities + needed typography/spacing tokens (light+dark); add the new primitives the direction needs (hero card, progress rail) and a single source-type identity helper (color+icon); add a dev-only style-reference surface rendering all of it in both modes as the de-risk gate; remove the dead PERSONA_SUBS export. ADDITIVE ONLY: do not restyle or touch existing screens; their tests must stay green untouched. Return the file list, the token additions, the new primitives, the identity helper, and confirm existing screens are untouched. Return the plan and STOP."

**After approval.** Execute, verify all gates green with count; confirm existing screens visually unchanged and their tests untouched; the style-reference renders in light and dark; the identity helper is the single mapping; PERSONA_SUBS gone. Commit "services/dis-ui: R1 new visual language tokens + style reference (additive)". Do not push; show diff and hash; stop.

---

## R2: the connector picker (net-new, additive)

**Goal.** Build the new Sources connector-picker screen in the new language: CSV upload as the full-width live hero, then a "Connect a POS or ERP system" group with Shopify POS, Square, and Other POS/ERP as coming-soon cards. Clicking CSV routes into the CSV journey (R3); the POS cards route to the thin connect step (R5).

**Why here.** Net-new screen: it cannot regress existing screens, and it is the first real product UI on the R1 language, validating the tokens and the source-type identities in situ.

**Scope.**
In:
- a connector-picker route (the "add a source" surface)
- the CSV hero card (live, routes to the journey), the POS group (Shopify POS, Square, Other as coming-soon cards), using the R1 source-type identities
- coming-soon cards have no faked connect; they route to the R5 thin connect step (or show a coming-soon state until R5 lands)
Out:
- the CSV journey itself (R3), the thin connect step internals (R5), the manage-sources list (R4)

**Hard constraints.**
1. **Net-new, no regression (FM1).** Adds a screen and a route; does not change existing screens' behavior. Existing tests stay green.
2. **Coming-soon is honest (FM2).** POS cards are clearly coming-soon, no faked connect affordance.
3. Uses the R1 identity helper (no duplicated colors/icons).
4. Standing discipline + light/dark.

**Acceptance criteria.**
1. The connector picker renders: CSV hero (routes toward the journey), and Shopify POS / Square / Other as coming-soon cards, in the new language, light and dark.
2. CSV hero navigation targets the CSV journey entry (upload); POS cards target the thin connect step (or a coming-soon state pre-R5).
3. Uses the R1 source-type identity helper.
4. Net-new; existing screens and tests unchanged.
5. All gates green; no em-dashes; correct naming.

**Failure modes.** FM1: regressing existing screens (net-new only). FM2: faking a POS connect. FM3: duplicating identity styling instead of the helper.

**Plan-mode prompt.** "Read the skill, this slice, redesign-design-direction.md (connector picker), the R1 tokens + identity helper + hero/card primitives, the route table, and the Sources nav. Plan to build the connector-picker screen: CSV hero routing to the CSV journey upload entry; Shopify POS / Square / Other coming-soon cards routing to the thin connect step (or a coming-soon state pre-R5); using the R1 identity helper; new route registered. Net-new: do not change existing screens. Tests: the picker renders the hero + coming-soon cards, CSV routes to the journey entry, POS routes to connect/coming-soon. Return file list, route, nav change, test list; confirm net-new. Plan and STOP."

**After approval.** Execute, gates green with count; confirm net-new and existing tests unchanged; light+dark; identity helper used. Commit "services/dis-ui: R2 connector picker (CSV hero, POS coming-soon)". Do not push; diff + hash; stop.

---

## R3: the CSV journey as a guided flow (the big reshape)

**Goal.** Rebuild the CSV upload-to-go-live flow as one guided journey behind the 4-step rail: Upload, Review mapping (AI-assisted, human-approved), Preview (dry-run canonical), Go live (mapping activation). Reshapes the existing Sample Upload and Mapping Review screens and the dry-run preview into a coherent flow in the new language. The fixture layer underneath stays.

**Why here.** This is the highest-touch, highest-regression-risk slice (real screens, real tests, behavioral change), so it lands once the visual language (R1) is stable and proven (R2), not bundled with cosmetics.

**Scope.**
In:
- the 4-step progress rail across the journey (the R1 rail primitive)
- step 1 Upload: the new dropzone, feeding the existing onboarding sample fixture flow (real upload behavior preserved; the screen reshaped)
- step 2 Review mapping: the AI-assisted framing (high-confidence calm, low-confidence pulled out for judgment, editable canonical targets, confidence signal), reshaping MappingReview; the underlying mapping fixture/draft logic stays
- step 3 Preview: the dry-run canonical preview (2.4), reshaped into the journey
- step 4 Go live: an honest mapping-activation completion state (not a faked live-ingestion monitor)
Out:
- changing the mapping fixture/draft data logic or the contract shapes (reshape the screens, keep the data layer)
- the live recurring ingestion / monitoring (deferred, no UI; Go live is activation only)
- LLM reasoning display the UI cannot back (AI-assisted framing only; do not show fabricated reasoning)

**Hard constraints.**
1. **Reshape screens, keep the data layer (FM1).** The onboarding/mapping fixture logic and contract shapes are unchanged; this slice changes presentation and flow, not the data contracts. Behavioral assertions about mapping data stay green; only layout/flow/selector changes.
2. **AI-assisted framing is honest (FM2).** Present the mapping as smart-assisted and human-approved; do NOT display reasoning the backend does not provide. Whether real LLM inference exists is a Sanjeev spec item.
3. **Go live is activation, not faked ingestion (FM3).** The completion state says the mapping is active and future files flow through it; it does not fake an ongoing-ingestion dashboard.
4. **The journey is coherent (FM4).** The 4 steps share the rail and the new language; the flow reads as one journey, not the old scattered screens.
5. Standing discipline + light/dark.

**Acceptance criteria.**
1. The journey renders behind the 4-step rail; the user can move Upload to Review to Preview to Go live.
2. Upload reshaped; the existing onboarding sample fixture behavior preserved (the screen still produces a sample/mapping via the fixture).
3. Review mapping shows the AI-assisted, human-approved framing (low-confidence rows pulled out, editable canonical targets, confidence); the mapping draft logic and its behavioral tests stay green.
4. Preview shows the dry-run canonical rows (2.4).
5. Go live is an honest activation completion state; no faked live-ingestion monitor.
6. The mapping/onboarding data contracts are unchanged; existing data-layer tests green; screen tests updated for the new flow (selector/flow only, not data assertions).
7. All gates green; light+dark; no em-dashes; correct naming.

**Failure modes.** FM1: changing the data/contract layer (reshape screens only). FM2: fabricating LLM reasoning. FM3: faking live ingestion at Go live. FM4: leaving the steps scattered instead of one rail-driven journey. FM5: breaking the mapping data assertions (only flow/selector tests change).

**Plan-mode prompt.** "Read the skill, this slice, redesign-design-direction.md (the CSV journey), the R1 rail/tokens/identity, the current SampleUpload + MappingReview + onboarding fixture + their tests, and the dry-run preview (2.4). Plan to rebuild the journey behind the 4-step rail: reshape Upload (keep the onboarding sample fixture behavior); reshape Review mapping with the AI-assisted human-approved framing (keep the mapping draft logic and its data tests); reshape Preview (dry-run canonical rows); add an honest Go-live activation state (no faked ingestion). RESHAPE SCREENS, KEEP THE DATA LAYER: do not change onboarding/mapping contract shapes; mapping data assertions stay green; only flow/selector tests change. Do not fabricate LLM reasoning. Return file list, the rail approach, what changes per screen, which tests change (flow/selector only) vs stay (data), and confirm the data layer is untouched. Plan and STOP."

**After approval.** Execute, gates green with count; confirm the data/contract layer unchanged and mapping data tests green; the journey reads as one rail-driven flow; AI-assisted framing carries no fabricated reasoning; Go live is activation only; light+dark. Commit "services/dis-ui: R3 CSV journey as a guided flow". Do not push; diff + hash; stop.

---

## R4: manage-sources screen + the form refactor

**Goal.** Build the manage-existing-sources screen (the other half of the Sources split) in the new language: the connected-sources list with health, edit, and deprecate (the slice 27 CRUD reshaped). Fold in the one real duplication the audit found: extract the SourceCreate / SourceEdit shared form into a single component.

**Why here.** A reshape of existing CRUD on the settled language; lower risk than R3, and the audit's one genuine refactor (the 32-line form clone) belongs with the screens that own it.

**Scope.**
In:
- the manage-sources screen: connected sources with their source-type identity, health, and edit/deprecate actions, in the new language
- extract a shared source-form component used by both create (in the connector/journey path) and edit, collapsing the SourceCreate/SourceEdit clone
- preserve the slice 27 behavior: deprecate-only (no hard delete), source_id immutable on edit, the one SourceDraft shape reconciled with onboarding
Out:
- changing the source CRUD data contracts or the SourceDraft shape (reshape + refactor, not a contract change)
- the connector picker (R2) or the journey (R3)

**Hard constraints.**
1. **Behavior preserved (FM1).** Deprecate-only, source_id immutable on edit, the shared SourceDraft shape: all unchanged. The refactor is structural (shared form component); behavioral assertions stay green.
2. **One form component (FM2).** SourceCreate and SourceEdit consume one shared form; the clone is gone. No behavior change from the extraction.
3. Uses the R1 identity helper for source-type display.
4. Standing discipline + light/dark.

**Acceptance criteria.**
1. The manage-sources screen lists connected sources with identity, health, and edit/deprecate, in the new language.
2. A single shared source-form component backs both create and edit; the SourceCreate/SourceEdit duplication is collapsed; behavior unchanged (deprecate-only, source_id immutable, one SourceDraft).
3. Existing source CRUD behavioral tests stay green (selector-only changes where the new layout requires).
4. All gates green; light+dark; no em-dashes; correct naming.

**Failure modes.** FM1: changing CRUD behavior or the SourceDraft shape (preserve). FM2: leaving the form clone (extract it). FM3: hard-delete creeping in (deprecate-only stays).

**Plan-mode prompt.** "Read the skill, this slice, redesign-design-direction.md (manage sources), the R1 tokens/identity, the current SourceCreate/SourceEdit/SourcesIndex + sources fixture + tests (note the audit's 32-line SourceCreate/SourceEdit clone). Plan to: build the manage-sources screen (connected sources, identity, health, edit/deprecate) in the new language; extract ONE shared source-form component for create+edit, collapsing the clone, with NO behavior change (deprecate-only, source_id immutable, one SourceDraft). Reshape + refactor, not a contract change. Tests: CRUD behavioral assertions stay green (selector-only where layout requires); a test confirms create and edit use the shared form. Return file list, the shared-form extraction, what reshapes, which tests change (selector-only) vs stay. Plan and STOP."

**After approval.** Execute, gates green with count; confirm CRUD behavior preserved (deprecate-only, source_id immutable, one SourceDraft), the form clone collapsed, behavioral tests green; light+dark. Commit "services/dis-ui: R4 manage-sources screen + shared source-form refactor". Do not push; diff + hash; stop.

---

## R5: the thin POS connect-step pattern

**Goal.** Build the coming-soon POS connection-setup pattern (Shopify POS, Square, Other) in the new language: a connect step (step 1 of the same journey rail) showing the credential shell as the planned shape, clearly coming-soon and disabled, with a notify-me action, and the framing that once connected it hands off to the SAME mapping/preview/go-live journey as CSV. Credentials-only; location-to-store_id mapping deferred. The credential shapes are specs for Sanjeev.

**Why here.** Net-new and thin (low risk), and it depends on the journey (R3) existing so the "hands off to the shared journey" framing is real, and on the connector picker (R2) routing into it.

**Scope.**
In:
- a thin connect-step screen, parameterized by POS type (Shopify POS, Square, Other), showing the representative credential shell (disabled), coming-soon, notify-me, and the shared-journey handoff note
- the credential shapes captured as flagged provisional constants (the spec surface for Sanjeev: per-POS auth fields)
Out:
- any working connection, OAuth handshake, or sync (thin only)
- the location-to-store_id mapping step (deferred; recorded as a future step and Sanjeev spec item)
- building per-POS deep journeys (they reuse the R3 journey once real)

**Hard constraints.**
1. **Thin and honest (FM1).** No working connect, no faked sync. Disabled credential shell, coming-soon, notify-me. The shell is the planned shape, not a live integration.
2. **Credential shapes are flagged specs (FM2).** The per-POS credential fields are flagged provisional constants, recorded as Sanjeev's to confirm (auth model per POS). The UI proposes; he confirms.
3. **Shared-journey handoff is stated, not built (FM3).** The note that a connected POS feeds the same mapping journey is framing; this slice does not build a POS-specific journey.
4. **Location-to-store_id deferred (FM4).** Not built; recorded as a future step and a Sanjeev spec item (touches identity_mirror).
5. Uses the R1 identity helper. Standing discipline + light/dark.

**Acceptance criteria.**
1. The connect step renders for Shopify POS / Square / Other in the new language, coming-soon, with a disabled representative credential shell and a notify-me action.
2. The shared-journey handoff note is present; no POS-specific journey is built.
3. The per-POS credential shapes are flagged provisional constants recorded as Sanjeev specs.
4. No working connect/sync; location-to-store_id not built (recorded as deferred).
5. Net-new/thin; existing screens and tests unchanged. All gates green; light+dark; no em-dashes; correct naming.

**Failure modes.** FM1: building a real/faked connect. FM2: asserting credential shapes instead of flagging them as specs. FM3: building a POS journey instead of reusing R3. FM4: building the location mapping (deferred). FM5: regressing existing screens (net-new).

**Plan-mode prompt.** "Read the skill, this slice, redesign-design-direction.md (thin POS connector pattern), the R1 tokens/identity, the R2 connector picker routing, and the R3 journey (for the handoff framing). Plan to build a thin connect-step screen parameterized by POS type (Shopify POS, Square, Other): a disabled representative credential shell, coming-soon, notify-me, and a note that a connected POS feeds the same R3 journey. Capture per-POS credential shapes as flagged provisional constants (Sanjeev specs). THIN ONLY: no working/faked connect, no POS-specific journey, no location-to-store_id mapping (record as deferred + Sanjeev spec). Net-new: do not change existing screens. Tests: the connect step renders coming-soon for each POS type, the credential shell is disabled, the handoff note is present. Return file list, the credential spec constants, the route(s), test list; confirm thin and net-new. Plan and STOP."

**After approval.** Execute, gates green with count; confirm thin (no working/faked connect), credential shapes flagged as specs, no POS journey built, location mapping deferred, net-new and existing tests unchanged; light+dark. Commit "services/dis-ui: R5 thin POS connect-step pattern (coming-soon)". Do not push; diff + hash; stop.

---

## R6: the richer Dashboard

**Goal.** Reshape the tenant Dashboard into the analytical overview in the new language: top-line metric cards, the "where your data comes from" source-type breakdown (with not-yet-connected types as dimmed roadmap rows), and the health-by-source rows (keeping the slice 28 actionable links). Mostly additive on an already-actionable screen.

**Why last.** Lowest risk, and it benefits from the source-type identities (R1) and the connector set (R2/R5) being settled, so the breakdown and the dimmed roadmap rows are consistent with the rest of the product.

**Scope.**
In:
- the top-line metric cards (rows ingested, active sources as "N of M types", in quarantine, P95 latency) in the new language
- the "where your data comes from" panel: ingestion volume by source type, with connected types shown with their identity and not-yet-connected types (Shopify POS, Square, Other) as dimmed "not connected" roadmap rows
- the health-by-source rows reshaped, KEEPING the slice 28 actionable links (source to mappings, quarantine count to filtered Quarantine) and the source-type identity
Out:
- changing the dashboard data contract (reshape + additive panel; the fixture stays, extended additively if needed for the breakdown)
- the live-ingestion analytics that have no backend (the breakdown uses the existing rollup data; do not invent live metrics)

**Hard constraints.**
1. **Keep slice 28 actionability (FM1).** The source-to-mappings and quarantine-count-to-filtered-Quarantine links (matched on source_id) stay working; those tests stay green.
2. **Additive breakdown (FM2).** The source-type breakdown uses existing rollup data (extended additively in the fixture if needed); do not invent live metrics with no backing. Not-yet-connected types are clearly dimmed/roadmap, not faked data.
3. Uses the R1 identity helper. Standing discipline + light/dark.

**Acceptance criteria.**
1. The Dashboard renders the metric cards, the source-type breakdown (connected types with identity + volume; not-connected types as dimmed roadmap rows), and the actionable health-by-source rows, in the new language.
2. The slice 28 links still work (source to mappings; quarantine count to /quarantine?source=<source_id>), matched on source_id; their tests stay green.
3. The breakdown uses existing rollup data (additive fixture extension if needed), not invented live metrics.
4. All gates green; light+dark; no em-dashes; correct naming.

**Failure modes.** FM1: breaking the slice 28 actionable links. FM2: inventing live metrics with no backend (use the rollup; dim the unconnected). FM3: duplicating source-type styling instead of the helper.

**Plan-mode prompt.** "Read the skill, this slice, redesign-design-direction.md (richer Dashboard), the R1 tokens/identity, the current Dashboard + its fixture + tests (note the slice 28 actionable links matched on source_id), and the connector set. Plan to reshape the Dashboard in the new language: metric cards; a 'where your data comes from' source-type breakdown (connected types with identity + volume from the existing rollup; Shopify POS/Square/Other as dimmed not-connected roadmap rows); health-by-source rows keeping the slice 28 links (source to mappings, quarantine count to filtered Quarantine, matched on source_id). Additive: use existing rollup data, extend the fixture additively only if needed, do not invent live metrics. Tests: the slice 28 link tests stay green; the breakdown renders connected + dimmed types. Return file list, the breakdown approach, any additive fixture change, which tests stay green vs change (selector-only). Plan and STOP."

**After approval.** Execute, gates green with count; confirm the slice 28 actionable links work and their tests green; the breakdown uses rollup data (no invented live metrics); not-connected types dimmed; light+dark. Commit "services/dis-ui: R6 richer Dashboard (source-type analytics)". Do not push; diff + hash; stop.

---

## Sequencing and dependencies (recap)

- **R1 first** (foundation: tokens + identity helper + primitives, de-risked, additive). Everything depends on it.
- **R2** (connector picker, net-new) exercises R1 on real product UI, low risk.
- **R3** (CSV journey, the big reshape) once the language is stable; highest regression risk; reshape screens, keep the data layer.
- **R4** (manage-sources + form refactor) reshapes existing CRUD; folds in the audit's one real duplication.
- **R5** (thin POS connect) net-new/thin; depends on R2 (routing) and R3 (handoff framing).
- **R6** (richer Dashboard) last; lowest risk; benefits from the identities and connector set being settled.
- Anything Sanjeev-dependent (POS credential contracts, the connector-feeds-same-pipeline assumption, location-to-store_id) stays thin/specced and never blocks; these feed the open-items register.

## What this reshape does NOT do
- It does not rebuild the fixture layer (the audit confirmed it is sound, intentional staging).
- It does not remove the real-mode HTTP seam (client.ts/request/getBaseUrl) which is deliberate scaffolding for slice 13.
- It does not change contract shapes (the real-mode switch, post-Sanjeev, does that).
- It does not build POS integrations, live ingestion, or location mapping (vision-thin; specced for Sanjeev).
