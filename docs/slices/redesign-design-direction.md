# DIS UI redesign: design direction

**Status:** locked direction, internal. This records what was decided in the direction-setting session (mockups + discussion). It is NOT a build plan and does NOT assume the codebase audit results; the scoping session turns this plus the audit into slices.

## The decisions that frame everything

1. **Vision-led but thin.** The UI presents the full multi-source product (CSV plus POS/ERP connectors), but only CSV is built deep and real end to end. POS connectors are built thin: a connection-setup shell shown as coming-soon, with the credential shape serving as a spec for Sanjeev, NOT a faked working integration. This keeps the product vision visible without writing speculative journey code for backends that do not exist.

2. **Both the design language and the screen design are in scope.** The austere admin-frontend-derived look is being replaced with a more characterful language (more color, distinct per-connector identity, stronger hierarchy, more breathing room), while staying clean and flat. Light and dark both required (the current UI is light+dark; the redesign keeps that).

3. **The sidebar does not change.** Nav stays as it is.

4. **Audit and redesign proceed together.** A read-only redundancy/LOC audit (knip, ts-prune, jscpd, depcheck, madge) runs in parallel to tell us what existing code is reusable, refactorable, or junk, and to set LOC boundaries from evidence rather than a target.

## The product model (corrected)

Every source is a channel of the same canonical retail SALES data (sku_id, quantity, store_id, source_sale_timestamp, the price fields, product_description). Sources differ only in how the data arrives:
- **CSV upload** (file based): real and live now.
- **POS / ERP systems** (API based): Shopify POS, Square, and other POS/ERP systems. These are point-of-sale systems for in-store sales (NOT e-commerce order data). Coming soon.

Because the canonical target is identical for every source, all sources converge on ONE shared mapping-and-approval journey. A connector only owns its own connection step; everything downstream (mapping, preview, go-live) is shared. This is the architectural spine of the redesign and the thing that stops N connectors from becoming N duplicate journeys.

## The visual language

- Card-based, clean, flat, generous whitespace. No dense utilitarian tables as the primary surface.
- Each connector/source type carries its own color identity (CSV, Shopify POS, Square, Other each visually distinct), used consistently across the connector picker, the Dashboard breakdown, and source rows, so the screens feel like one product.
- The live source (CSV) gets hero treatment; coming-soon connectors are clearly secondary (quiet "Soon" markers, no faked affordances).
- A consistent 4-step progress rail for the source journey (Connect/Upload, Review mapping, Preview, Go live) so the flow is legible.
- The exact brand palette and typography are pinned during the build with the real design tokens. The mockups showed the LEVEL of color and personality, not the final hex values. The real connector colors should use each system's own brand color where sensible.

## The screens

### Sidebar
No change.

### Dashboard (richer)
The tenant landing page becomes an analytical overview, not a read-only rollup:
- top-line metric cards (rows ingested, active sources as "N of M types", in quarantine, P95 latency)
- a "where your data comes from" panel: ingestion volume broken down BY SOURCE TYPE (CSV, Shopify POS, Square), with the not-yet-connected types shown as dimmed "not connected" rows so the panel advertises the roadmap
- health-by-source rows, clickable (a healthy source links to its mappings; a source with quarantine links to filtered Quarantine), carrying forward the slice 28 actionable-dashboard work
- an "Add source" affordance routing to the connector picker

### Sources, split into two screens
The decision was to SPLIT add-new from manage-existing (not one combined screen):
- **Connector picker** (add new): CSV upload as the full-width live hero, then a "Connect a POS or ERP system" group with Shopify POS, Square, and Other POS/ERP as coming-soon cards. Clicking CSV launches the upload journey; the POS cards lead to the thin connection-setup step.
- **Manage sources** (existing): the list of connected sources with health and management (edit/deprecate), the CRUD surface from slice 27, in the new language.

### The CSV journey (deep, real, end to end)
A guided journey behind the 4-step rail:
1. **Upload**: an inviting dropzone; the file lands in onboarding-staging GCS; DuckDB parses it. Sets expectations for what comes next.
2. **Review mapping (AI-assisted, human-approved)**: the system proposes column-to-canonical mappings; high-confidence rows are calm, low-confidence rows are pulled out for the user's judgment (editable, with the canonical target and a confidence signal). The framing is "we did the work, you verify the uncertain bits," not a wall of confidence numbers. Approve to continue.
3. **Preview**: the dry-run canonical preview (10 to 20 rows in canonical shape under the approved mapping), the "see it before you commit" payoff. Backed by the 2.4 contract.
4. **Go live**: an honest completion state. Approving makes the mapping active; future files for this source flow through it. This is mapping-activation, NOT a faked live-ingestion monitor.

### The POS connector thin path (pattern for Shopify POS, Square, Other)
- A connection-setup step (step 1 of the same rail) showing the credential shell as the planned shape, clearly coming-soon, disabled, with a "notify me when ready" action instead of a working connect.
- Once connected (in the future), the connector hands off to the SAME mapping/preview/go-live journey as CSV. The connector owns only its connect step.
- Credentials-only for now. The exact per-connector credential contract (Shopify POS API auth, Square API auth) is a spec item.

## Honest scope boundaries (real vs vision vs deferred)

- **Real and built deep:** the CSV journey through upload, AI-assisted mapping, preview, and go-live-as-mapping-activation. This path exists in the backend architecture.
- **Vision, built thin:** the POS connectors (Shopify POS, Square, Other). Connection-setup shell only, coming-soon, credential shape as spec. No faked integration or sync.
- **Deferred (not built, recorded):**
  - the recurring LIVE ingestion that flows through an active mapping over time, and any monitoring of it (the live-ingestion path has no UI and a fuzzy backend; flagged for Sanjeev)
  - POS location-to-store_id mapping (POS systems have a locations concept that maps to our store_id; skipped in the thin build, a known future step and likely a Sanjeev spec item since it touches identity_mirror/store resolution)

## Spec items this redesign hands to Sanjeev (feeds the open-items register)

- Does connector-pulled data feed the SAME canonical mapping/validation pipeline as an uploaded CSV? (the shared-journey assumption; architecturally sensible, needs confirming)
- The per-POS credential contract for Shopify POS, Square, and a generic POS/ERP (auth model, fields).
- POS location-to-store_id resolution (when POS connectors get real; touches identity_mirror).
- Is the mapping inference actually LLM-driven, and can it surface reasoning? (the UI presents it as AI-assisted; the architecture says DuckDB inference + suggestion, does not say LLM. The UI must not show reasoning it cannot back.)
- The live-ingestion upload/monitoring path (still the missing-screen item from the build).
- These join the existing open-items register (source schema, registration, store semantics, the FTP/API ingestion scope now refined to POS/ERP, etc.).

## What the scoping session needs

- this doc (the locked direction)
- the audit results (LOC inventory, dead code, duplication, module sprawl) to decide what is reused, refactored, or discarded, and to set LOC boundaries from evidence
- the open-items register (so the redesign's connector specs flow to Sanjeev)

The scoping session turns these into slices.
