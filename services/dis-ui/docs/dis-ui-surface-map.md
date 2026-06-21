# DIS UI Surface Map: status and authority (added 2026-06-03)

This is a v1.0 UX design artifact: screens, wireframes, user journeys, audiences, per-screen data and actions, and the phased build sequence. Those remain authoritative for screen design.

It predates the reconciliation to Sanjeev's foundation. Where this document and the items below disagree, the items below win:

- Endpoint contracts: `docs/ui-engineer-demand-list.md` is authoritative. The per-screen "dis-api dependencies" listed here are superseded proposals; use the demand list paths and shapes.
- Auth and RBAC: `services/dis-ui/docs/dis-ui-server-contract.md` and the reconciled token model are authoritative. The token carries `sub`, `tenant_id`, `store_id`, and a `roles` array in the `dis:<capability>` namespace; the tenant-versus-ops split is the `dis:ops` role, gated via `isOps`. Section 6.2's `userType`/`PLATFORM` and the admin-frontend 4-tuple permissions are superseded.
- Naming: `dis-api` is now `dis-ui-server`; `ithina-dis/ui/` is now `services/dis-ui/`. Decisions cited here as D1 to D33 should be read against the current `docs/decisions.md` (through D37).

Read this document for what each screen should look like and do. Read the demand list and the contract doc for what to call and how auth works.

---

# DIS UI Surface Map v1.0

**Status:** v1.0 draft, informed by Sanjeev's locked DIS architecture (architecture.md, decisions.md, build-guide.md, repo-structure.md, engineering-reference.md, cost-estimate.md).

**Purpose:** Catalog the screens needed in the DIS UI (`ithina-dis/ui/`) for v1.0 launch. Describes user workflows, audiences, screen-level structure, key data, and actions. Informs Sanjeev's dis-api endpoint design and the eventual ithina-dis/ui/ build sequencing.

**Scope.** v1.0 only. Deferred surfaces (POS API receiver, ERP CSV POST, reverse-API puller) are out of scope until those receivers ship.

**Auth model.** JWT-based for v0 dev (same persona pattern as admin-frontend stub mode). Customer Master Auth0 integration when Sanjeev ships it.

**Companion docs (in ithina-dis monorepo when it exists).**
- architecture.md (the WHY)
- decisions.md (D1-D33)
- build-guide.md (phases + cadence)
- repo-structure.md (file layout)
- engineering-reference.md (per-service reference)
- cost-estimate.md (infra projection)

---

## 1. Audiences

Two distinct user audiences, served by the same DIS UI container with different RBAC-gated access.

### Tenant operator
The customer's user. Examples: retail-chain data team member, store ops lead.

**Cares about:** is my data flowing? where are my errors? how do I fix and resubmit?

**Permission scope:** TENANT (own tenant only, RLS-enforced by backend).

**Sees:** Tenant Dashboard, Sources, Sample Upload, Mapping Review, Quarantine Console (tenant slice), Notifications, Audit Lookup (own-tenant only).

**Does not see:** Ops Fleet, DuckDB query panel, cross-tenant quarantine.

### Ithina ops
The Sevyn8 internal operator. Examples: Sanjeev, Anjali, support engineers.

**Cares about:** are all tenants healthy? what's broken? trace this row's lifecycle.

**Permission scope:** PLATFORM (cross-tenant, full visibility).

**Sees:** everything tenant sees + Ops Fleet, cross-tenant Quarantine, DuckDB query panel, ops-driven Onboarding flow.

---

## 2. Screen inventory at a glance

| # | Screen | Audience | Sanjeev sub-module mapping |
|---|---|---|---|
| 1 | Tenant Dashboard | Tenant | (not in 7-module list) |
| 2 | Sources & Connections | Tenant + Ops | (not in 7-module list; conceptually adjacent to Mapping CRUD) |
| 3 | Sample Upload | Tenant + Ops | Sample upload |
| 4 | Mapping Review | Tenant + Ops | Onboarding review (stage 1) |
| 5 | Shadow Rollout Review | Tenant + Ops | Onboarding review (stage 2) |
| 6 | Mapping Versions & CRUD | Tenant + Ops | Mapping config CRUD |
| 7 | Quarantine Console | Tenant + Ops | Quarantine console |
| 8 | Audit & Trace Lookup | Tenant + Ops | Audit & trace lookup |
| 9 | Notifications | Tenant | (not in 7-module list; implied) |
| 10 | Ops Fleet | Ops | (not in 7-module list; recommended) |
| 11 | DuckDB Query Panel | Ops | DuckDB query panel |

**Expansion summary:** Sanjeev's 7 sub-modules become ~11 distinct screens because:
- Onboarding workflow naturally splits into Sample Upload → Mapping Review → Shadow Rollout Review (3 screens, 1 sub-module name).
- Mapping CRUD has both a registry view (Sources & Connections) and a version-history view (Mapping Versions & CRUD).
- Tenant Dashboard, Notifications, and Ops Fleet are implied UX expectations that the architecture docs don't explicitly name.

---

## 3. Tenant journeys

### Journey A: Onboarding a new source (self-serve flow)

A tenant operator wants to connect their POS CSV feed to DIS.

```
[Tenant lands on]  Tenant Dashboard
        |
        v
[Clicks]           "Add new source" CTA  →  Sources & Connections
        |
        v
[Clicks]           "+ New source"
        |
        v
[Lands on]         Sample Upload
        |
        v
[Uploads CSV]      → DuckDB infers schema, dis-api proposes mapping
        |
        v
[Lands on]         Mapping Review
        |
        v
[Reviews]          column-by-column proposed mapping
                   overrides low-confidence rows
                   dry-run preview against sample
        |
        v
[Clicks]           "Approve to staged"
        |
        v
[Notification]     "Mapping approved. Shadow rollout starting."
        |
        v ... (some time passes; shadow runs in background)
        |
[Notification]     "Shadow output ready for review"
        |
        v
[Lands on]         Shadow Rollout Review
        |
        v
[Reviews]          staged-output rows vs canonical expectations
        |
        v
[Clicks]           "Promote to active"  →  mapping goes live
        |
        v
[Back to]          Sources & Connections (source now shows "Active")
```

### Journey B: Fixing a quarantined row

Tenant gets a notification that rows failed.

```
[Notification]     "32 rows quarantined for source X"
        |
        v
[Clicks notification]
        |
        v
[Lands on]         Quarantine Console (tenant slice, pre-filtered)
        |
        v
[Sees]             list of failed rows with human-readable error reasons
        |
        v
[Clicks row]       row detail panel: original payload + error context
        |
        +--> Path A (fix at source):
        |       [Re-uploads corrected CSV via Sample Upload]
        |       → resubmit_type=fixed_file, chain_depth=1
        |
        +--> Path B (retry as-is, maybe transient):
                [Clicks "Resubmit"]
                → resubmit_type=replay, same mapping version
```

### Journey C: Debugging "is my data flowing?"

Tenant sees a row missing in their downstream system and wants to verify DIS received it.

```
[Tenant Dashboard] sees "Last submission: 12 min ago"
        |
        v
[Clicks]           "Audit trace lookup"
        |
        v
[Lands on]         Audit & Trace Lookup
        |
        v
[Searches by]      trace_id  OR  store_id + time range
        |
        v
[Sees]             lifecycle: received → validated → mapped → canonical
                   (or)        received → validated → quarantined
                   with timestamps + mapping_version_id per stage
```

---

## 4. Ops journeys

### Journey D: Ops onboards a new tenant's source (ops-driven flow)

Ithina ops uploads the sample on the tenant's behalf, typically for complex or first-time sources.

```
[Lands on]         Ops Fleet
        |
        v
[Picks tenant]     "Acme Retail" → tenant context loaded
        |
        v
[Same flow as Journey A from Sample Upload onwards, scoped to picked tenant]
```

### Journey E: Ops investigates a broken mapping

Ops gets paged: tenant X's mapping is producing nonsense canonical rows.

```
[Lands on]         Ops Fleet
        |
        v
[Sees]             "Tenant X: 87% quarantine rate last 1h" (red)
        |
        v
[Clicks]           tenant row → Quarantine Console (ops slice, filtered)
        |
        v
[Inspects rows]    sees pattern: "expected DD-MM, got MM-DD" on all rows
        |
        v
[Clicks]           "View mapping" → Mapping Versions & CRUD
        |
        v
[Sees]             current active mapping v3; v2 had different date format
        |
        v
[Decision tree]:
        |
        +--> Path A: rollback to v2
        |       [Edit mapping] → creates v4 (copy of v2) → promote to active
        |
        +--> Path B: ops-side replay against a fixed v5
                [Edit mapping] → creates v4 (fix the date transform)
                → promote to active
                → [DuckDB Query Panel] inspect a couple of bronze chunks
                → [Audit & Trace Lookup] kick replay for last 24h chunks
```

### Journey F: Ops debugs a single row's lifecycle

Tenant escalates "this specific order is wrong in my downstream."

```
[Lands on]         Audit & Trace Lookup (ops view, cross-tenant)
        |
        v
[Searches]         trace_id OR (tenant_id + store_id + time + sku)
        |
        v
[Sees]             full row lifecycle including:
                   - which mapping_version_id processed it
                   - any DUPLICATE_NOOP or DUPLICATE_OVERWRITTEN events
                   - prior_trace_id of the original (if a correction)
                   - per-stage timestamps and status
        |
        v
[If still unclear, clicks]  "Inspect bronze blob"
        |
        v
[Lands on]         DuckDB Query Panel
        |
        v
[Pastes]           GCS URI from audit row + SQL query
        |
        v
[Sees]             raw row contents as received
```

---

## 5. Screen-by-screen specifications

### Screen 1: Tenant Dashboard

**Audience:** Tenant operator.
**Route:** `/` (default landing after auth for tenant role).
**Purpose:** Answer "is my data flowing right now?" at a glance. Surface anything that needs attention.

**Key data displayed:**
- Last successful submission timestamp + count (per source, last 24h)
- Currently quarantined row count (per source)
- Active sources count + health badges
- Recent notifications (3-5 most recent)
- Latency snapshot: p50/p95/p99 last 1h (high-level, no SLO details)

**Key actions:**
- "Add new source" → Sources & Connections
- "View quarantine" → Quarantine Console
- "View notifications" → Notifications
- Click a source health badge → Sources & Connections detail

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Welcome back                                       │
│ ----------------│                                                     │
│ [*] Dashboard   │  ┌─ Last activity ──────────────────────────────┐   │
│ [-] Sources     │  │ 12 min ago · POS-CSV-Main · 1,247 rows ok   │   │
│ [-] Upload      │  │ 2h ago · ERP-Daily · 18,402 rows ok         │   │
│ [-] Quarantine  │  └──────────────────────────────────────────────┘   │
│     (32 new)    │                                                     │
│ [-] Audit       │  ┌─ Health by source ───────────────────────────┐   │
│ [-] Notifs (3)  │  │ POS-CSV-Main      ● Healthy   1.247k/24h     │   │
│                 │  │ ERP-Daily         ● Healthy   18.4k/24h      │   │
│                 │  │ Shopify-Online    ⚠ 32 quarantined           │   │
│                 │  └──────────────────────────────────────────────┘   │
│                 │                                                     │
│                 │  ┌─ Recent notifications ───────────────────────┐   │
│                 │  │ ⚠  32 rows quarantined · Shopify · 4m ago   │   │
│                 │  │ ✓  Mapping v3 promoted to active · 2h ago    │   │
│                 │  │ ⚠  Shadow output ready for review · 6h ago   │   │
│                 │  └──────────────────────────────────────────────┘   │
│                 │                                                     │
│                 │  ┌─ Latency last 1h ────────────────────────────┐   │
│                 │  │ p50: 2.1s   p95: 6.8s   p99: 11.2s           │   │
│                 │  └──────────────────────────────────────────────┘   │
│                 │                                                     │
│                 │  [+ Add new source]   [View all quarantine]         │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `GET /dashboard/summary` (per-tenant overview; aggregate audit + quarantine counts)
- `GET /sources?status=active` (health badges)
- `GET /notifications?limit=5` (recent only)

---

### Screen 2: Sources & Connections

**Audience:** Tenant + Ops.
**Route:** `/sources`.
**Purpose:** Registry of configured data sources for the tenant. Tenant sees own sources; ops sees cross-tenant filterable.

**Key data displayed:**
- Source name, type (POS / ERP / CSV / API), store association
- Status (Active / Staged / Deprecated / Failing)
- Last successful submission timestamp
- Active mapping version
- Quarantine rate (last 24h)

**Key actions:**
- "+ New source" → Sample Upload (start onboarding)
- Click source row → Source detail with mapping version history + recent activity
- Edit source metadata (display name, contact, store association)
- Deprecate source (soft-disable; new submissions rejected)

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Sources & Connections                              │
│                 │                                                     │
│                 │  [+ New source]              [Filter ▼]  [Search]   │
│                 │                                                     │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ Name           Type   Store     Status  v   Q% │ │
│                 │  ├────────────────────────────────────────────────┤ │
│                 │  │ POS-CSV-Main   CSV    Store-01  ●Active 3   0% │ │
│                 │  │ ERP-Daily      CSV    All       ●Active 2   0% │ │
│                 │  │ Shopify-Online API    Online    ⚠Failing 5  8% │ │
│                 │  │ ERP-Returns    CSV    Store-02  ●Active 1   0% │ │
│                 │  │ Square-NY      API    Store-NY  ◌Staged  1   - │ │
│                 │  └────────────────────────────────────────────────┘ │
│                 │                                                     │
│                 │  Ops-only filter: [All tenants ▼]                   │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `GET /sources` (list, RLS-scoped or PLATFORM cross-tenant)
- `GET /sources/{id}` (detail)
- `PATCH /sources/{id}` (metadata edits)
- `POST /sources/{id}/deprecate`

**Notes:**
- This screen is conceptually adjacent to Mapping CRUD but oriented around the SOURCE (the data feed identity), not the MAPPING (the transform config). A source has many mapping versions over time.
- For ops: dropdown filter to switch tenants.

---

### Screen 3: Sample Upload

**Audience:** Tenant + Ops.
**Route:** `/upload`.
**Purpose:** Onboarding step 1 - accept a CSV sample, store to GCS, trigger schema inference.

**Key data displayed:**
- Drop zone for CSV file
- (Alternative) paste raw payload textarea
- Source name + type selector (creates new Source record OR attaches sample to existing source)
- Upload progress + DuckDB inference progress

**Key actions:**
- Drop or browse file (.csv only for v1.0; mime check at receiver)
- Set source name + type
- Click "Analyze sample" → triggers schema inference (~5-30s)
- After inference completes → auto-navigate to Mapping Review

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                            [bell] [profile]         │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Sample Upload                                      │
│                 │                                                     │
│                 │  Step 1 of 3: Upload a sample of your data          │
│                 │                                                     │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │                                                │ │
│                 │  │              [↑ Drop CSV file here]            │ │
│                 │  │              or click to browse                │ │
│                 │  │                                                │ │
│                 │  │              Max 10MB · .csv only              │ │
│                 │  └────────────────────────────────────────────────┘ │
│                 │                                                     │
│                 │  Source name:  [POS-CSV-Main_______________]        │
│                 │  Source type:  [POS CSV ▼]                          │
│                 │  Attach to:    [(•) New source  ( ) Existing ▼]     │
│                 │                                                     │
│                 │  [Cancel]                          [Analyze sample] │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `POST /uploads` (multipart, stores to GCS, returns upload_id)
- `POST /uploads/{id}/analyze` (kicks off DuckDB inference; returns job_id)
- `GET /uploads/{id}/analysis` (polls for completion; returns inferred schema)

**Notes:**
- Per architecture §5.1 + decisions.md D26, sample upload is the entry point to the onboarding sub-module of dis-api.
- Uploaded payload goes to GCS under onboarding-staging path; not into bronze (this is pre-mapping, exploratory).

---

### Screen 4: Mapping Review

**Audience:** Tenant + Ops.
**Route:** `/upload/{upload_id}/review`.
**Purpose:** Onboarding step 2 - review proposed mapping side-by-side with sample data, override low-confidence rows, dry-run, approve to staged.

**Key data displayed:**
- Per source column: inferred source-side type, sample values, null %, proposed canonical mapping, confidence score
- Normalization transforms suggested (date format, decimal sep, etc.)
- Dry-run preview of canonical rows
- Per-column "authoritative source" toggle (column ownership for column-scoped merge)

**Key actions:**
- Override per-column proposed mapping (dropdown of canonical columns)
- Edit transforms (date format picker, decimal separator, custom regex)
- Mark which canonical columns this source is authoritative for
- "Dry-run preview" → renders 10-20 canonical rows from sample
- "Approve to staged" → writes to `config.source_mappings` with `status='staged'`
- "Back to upload" → discard, retry with different sample

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Mapping Review · POS-CSV-Main                      │
│                 │                                                     │
│                 │  Step 2 of 3: Review proposed mapping               │
│                 │                                                     │
│                 │  ┌──────────────┬──────────────┬──────┬───────────┐ │
│                 │  │ Source col   │ Canonical    │ Conf │ Transforms│ │
│                 │  ├──────────────┼──────────────┼──────┼───────────┤ │
│                 │  │ item_code    │ sku_id       │ 98%  │ -         │ │
│                 │  │ sample: A123 │              │  ✓   │           │ │
│                 │  ├──────────────┼──────────────┼──────┼───────────┤ │
│                 │  │ qty          │ quantity     │ 95%  │ -         │ │
│                 │  │ sample: 12   │              │  ✓   │           │ │
│                 │  ├──────────────┼──────────────┼──────┼───────────┤ │
│                 │  │ txn_date     │ event_ts     │ 62%  │ Date fmt: │ │
│                 │  │ sample:      │              │  ⚠   │ DD-MM-YY  │ │
│                 │  │ 03-12-25     │              │      │ [Edit ▼]  │ │
│                 │  ├──────────────┼──────────────┼──────┼───────────┤ │
│                 │  │ pos_terminal │ store_id     │ 41%  │ -         │ │
│                 │  │ sample: T-2A │              │  ⚠   │ [Edit ▼]  │ │
│                 │  └──────────────┴──────────────┴──────┴───────────┘ │
│                 │                                                     │
│                 │  Authoritative columns: [☑ quantity] [☑ price]      │
│                 │                         [☐ sku_description]         │
│                 │                                                     │
│                 │  [Dry-run preview]  [Back]      [Approve to staged] │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `GET /uploads/{id}/analysis` (the inferred schema + proposed mapping)
- `PATCH /uploads/{id}/mapping` (operator overrides; updates draft)
- `POST /uploads/{id}/dry-run` (renders preview canonical rows from sample + mapping)
- `POST /uploads/{id}/approve` (writes to `config.source_mappings` with `status='staged'`)

**Notes:**
- Color/icon hints: high-confidence rows green check, low-confidence (<70%) yellow warning. Below 50%, red - likely operator must override.
- Column ownership (authoritative-for) is required for the column-scoped merge per decisions.md D8.

---

### Screen 5: Shadow Rollout Review

**Audience:** Tenant + Ops.
**Route:** `/sources/{id}/shadow-review` (or accessed from notification).
**Purpose:** Onboarding step 3 - review the staged mapping's output against expectations, before promoting to active.

**Key data displayed:**
- Staged mapping ran for N hours/days against real chunks
- Produced N canonical rows in `staging.*` schema
- Comparison vs current active mapping (if any): rows differ in X columns, Y rows
- Validation pass rate vs canonical-shape suite
- Sample diff rows (~10) showing staged vs active output side-by-side

**Key actions:**
- "Promote to active" → mapping goes live, replaces current active
- "Reject, iterate" → mapping marked deprecated, operator goes back to Mapping Review
- "Extend shadow window" → keep running staged for more time before deciding

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Shadow Review · POS-CSV-Main · v3 (staged)         │
│                 │                                                     │
│                 │  Step 3 of 3: Review shadow output, promote or      │
│                 │  iterate                                            │
│                 │                                                     │
│                 │  ┌─ Shadow stats ───────────────────────────────┐   │
│                 │  │ Window: last 48h · 3,124 input chunks        │   │
│                 │  │ Staged output: 18,402 rows                   │   │
│                 │  │ Validation pass rate: 99.4% (96 fails)       │   │
│                 │  │ Diff vs current active (v2):                 │   │
│                 │  │   · 12,011 rows identical                    │   │
│                 │  │   · 6,391 rows differ in 'event_ts'          │   │
│                 │  └──────────────────────────────────────────────┘   │
│                 │                                                     │
│                 │  ┌─ Diff samples (v2 → v3) ─────────────────────┐   │
│                 │  │ sku=A123  v2: 2026-03-12  v3: 2026-12-03  ⚠  │   │
│                 │  │ sku=B456  v2: 2026-03-08  v3: 2026-08-03  ⚠  │   │
│                 │  │ ...                                          │   │
│                 │  └──────────────────────────────────────────────┘   │
│                 │                                                     │
│                 │  [Reject, iterate] [Extend window] [Promote → live] │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `GET /sources/{id}/shadow-stats` (rollup of staged-window output)
- `GET /sources/{id}/shadow-diff?limit=10` (sample diffs vs current active)
- `POST /sources/{id}/promote` (staged → active; old active → deprecated; publishes `mapping.changed`)
- `POST /sources/{id}/reject` (staged → deprecated)

**Notes:**
- Per architecture §5.1 step 7-8 and decisions.md D6 (mapping.changed).
- When tenant has no prior active mapping (first onboarding), diff section is empty; only validation pass rate shown.

---

### Screen 6: Mapping Versions & CRUD

**Audience:** Tenant + Ops.
**Route:** `/sources/{id}/mappings`.
**Purpose:** View, edit, and manage mapping versions for a given source. Version history with active/staged/deprecated badges.

**Key data displayed:**
- Mapping version list with status, created date, created by, promoted/deprecated dates
- Per-version: field count, transforms count, validation suite version
- Current active highlighted; staged (if any) highlighted in different color

**Key actions:**
- View full mapping definition (read-only inspection)
- Edit mapping (creates new staged version; opens Mapping Review-like screen)
- Deprecate active mapping (ops only; rare, breaks ingestion until new mapping promoted)
- Inspect canonical rows by `mapping_version_id` (links to Audit & Trace Lookup filtered)

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Mappings · POS-CSV-Main                            │
│                 │                                                     │
│                 │  [+ New version]                                    │
│                 │                                                     │
│                 │  ┌──────────────────────────────────────────────┐   │
│                 │  │ v3  ● Active     2026-05-28 by anjali        │   │
│                 │  │     12 fields · 4 transforms · suite v3      │   │
│                 │  │     [View] [Edit (→ v4)] [Inspect rows]      │   │
│                 │  ├──────────────────────────────────────────────┤   │
│                 │  │ v2  ◌ Deprecated 2026-05-28 by anjali        │   │
│                 │  │     active 2026-04-10 to 2026-05-28          │   │
│                 │  │     [View] [Inspect rows]                    │   │
│                 │  ├──────────────────────────────────────────────┤   │
│                 │  │ v1  ◌ Deprecated 2026-04-10 by ops           │   │
│                 │  │     active 2026-03-01 to 2026-04-10          │   │
│                 │  │     [View] [Inspect rows]                    │   │
│                 │  └──────────────────────────────────────────────┘   │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `GET /sources/{id}/mappings` (version list)
- `GET /sources/{id}/mappings/{version}` (full definition)
- `POST /sources/{id}/mappings` (creates new staged version; opens edit flow)
- `POST /sources/{id}/mappings/{version}/deprecate` (ops-only)
- Link to Audit Lookup: `/audit?mapping_version_id={version}`

**Notes:**
- Per decisions.md D22, every canonical row carries `mapping_version_id`. This screen makes that traceable.
- "Edit mapping (→ v4)" deliberately creates a new version. Editing v3 in place is not allowed (versions are immutable per D22).

---

### Screen 7: Quarantine Console

**Audience:** Tenant (own-tenant slice) + Ops (cross-tenant slice).
**Route:** `/quarantine` (tenant) or `/quarantine?tenant_id=...` (ops).
**Purpose:** See failed rows, understand why they failed, resubmit corrected data.

**Key data displayed:**
- Quarantined row list with: trace_id, source, store, error reason (human-readable), failure stage (source-shape / canonical-shape / FK / normalization), failed_at timestamp
- Filters: source, error type, time range, status (open / resolved)
- Per-row detail: original payload, error context, mapping version that processed it
- Ops slice adds: tenant filter, "trigger Ithina-side replay" action

**Key actions:**
- Click row → inspect detail (raw payload, error reason, mapping version)
- "Resubmit (retry as-is)" → publishes `ingress.resubmit` with `resubmit_type=replay`
- "Re-upload corrected file" → navigates to Sample Upload with `parent_trace_id` link
- Ops only: "Mark resolved" (silently dismiss; no replay)
- Ops only: "Trigger Ithina-side replay" (against current active mapping or pinned)

**Wireframe (tenant slice):**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Quarantine                              32 open    │
│                 │                                                     │
│                 │  Filter: [All sources ▼] [All errors ▼] [Last 24h ▼]│
│                 │                                                     │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ Time     Source        Error             Trace │ │
│                 │  ├────────────────────────────────────────────────┤ │
│                 │  │ 4m ago   Shopify       Invalid price  ee...8e  │ │
│                 │  │ 4m ago   Shopify       Invalid price  ee...91  │ │
│                 │  │ 7m ago   POS-CSV-Main  Bad date fmt   fa...4c  │ │
│                 │  │ 15m ago  ERP-Daily     Missing sku    bd...3a  │ │
│                 │  │ ... (28 more)                                  │ │
│                 │  └────────────────────────────────────────────────┘ │
│                 │                                                     │
│                 │  Selected row detail ▼                              │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ Trace: ee...8e · Shopify · 4m ago · v5         │ │
│                 │  │ Error: price='12.5o' not a valid number        │ │
│                 │  │ Stage: canonical-shape validation              │ │
│                 │  │ Original payload (full):                       │ │
│                 │  │   {"sku":"A123", "price":"12.5o", ...}         │ │
│                 │  │                                                │ │
│                 │  │ [Resubmit as-is] [Re-upload corrected file]    │ │
│                 │  └────────────────────────────────────────────────┘ │
└─────────────────┴───────────────────────────────────────────────────┘
```

**Wireframe (ops slice - additional UI elements):**

```
                  │  Filter: [Tenant: All ▼] [Source ▼] [Error ▼] [Time ▼]
                  │
                  │  ┌─────────────────────────────────────────────────────┐
                  │  │ Time   Tenant         Source        Error      Trace│
                  │  ├─────────────────────────────────────────────────────┤
                  │  │ 4m ago Acme Retail    Shopify       Invalid    ...8e│
                  │  │ 4m ago Acme Retail    Shopify       Invalid    ...91│
                  │  │ 12m ago Beta Stores   POS-CSV       Bad date   ...3c│
                  │  │ ...                                                  │
                  │  └─────────────────────────────────────────────────────┘
                  │
                  │  [Resubmit] [Mark resolved] [Trigger Ithina-side replay]
```

**dis-api dependencies:**
- `GET /quarantine` (RLS-scoped for tenant; cross-tenant for ops with tenant_id filter)
- `GET /quarantine/{trace_id}` (detail, full payload)
- `POST /quarantine/{trace_id}/resubmit` (publishes `ingress.resubmit`)
- `POST /quarantine/{trace_id}/resolve` (ops-only, dismiss without replay)

**Notes:**
- Per decisions.md D6/D27, resubmit publishes `ingress.resubmit` with proper `resubmit_type` (replay vs fixed_file).
- "Chain depth" capping (architecture §6.5: cap at 3) is enforced backend-side; UI just shows a notice if a row's been resubmitted multiple times.

---

### Screen 8: Audit & Trace Lookup

**Audience:** Tenant (own-tenant) + Ops (cross-tenant).
**Route:** `/audit`.
**Purpose:** Query BigQuery `audit_events` by trace_id, tenant, store, or time range. Render per-stage lifecycle of a chunk or row.

**Key data displayed:**
- Search inputs: trace_id, tenant_id (ops), store_id, source_id, time range, status filter
- Result list: rows with trace_id, source, status, stage, timestamp, mapping_version_id
- Per-trace lifecycle view: ordered stages from received → ... → committed (or quarantined)

**Key actions:**
- Search by trace_id (direct lookup)
- Filter by tenant/store/time range
- Click row → expand to show full lifecycle (received → validated → mapped → committed, with mapping_version_id, error_code, timestamps per stage)
- Link to bronze inspection (Ops: opens DuckDB Query Panel with GCS URI prefilled)

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Audit & Trace Lookup                               │
│                 │                                                     │
│                 │  Trace ID:  [_________________________]  [Search]   │
│                 │                                                     │
│                 │  Or filter:                                         │
│                 │   Store:    [All ▼]   Source: [All ▼]               │
│                 │   Time:     [Last 24h ▼]   Status: [All ▼]          │
│                 │   (ops: Tenant: [Acme Retail ▼])                    │
│                 │                                                     │
│                 │  Results:                                           │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ Trace          Source       Status   Stage     │ │
│                 │  ├────────────────────────────────────────────────┤ │
│                 │  │ ee2a4...8e92   Shopify      ✗ Failed canonical │ │
│                 │  │ fa17b...4c08   POS-Main     ✓ Committed         │ │
│                 │  │ bd09c...3a01   ERP-Daily    ✗ Failed source     │ │
│                 │  └────────────────────────────────────────────────┘ │
│                 │                                                     │
│                 │  Selected trace lifecycle ▼                         │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ ee2a4...8e92 · Shopify · 4m ago                │ │
│                 │  │                                                │ │
│                 │  │ ✓ Received   2026-05-28 14:32:11.034           │ │
│                 │  │ ✓ Source-shape valid                           │ │
│                 │  │ ✓ Mapped     v5    14:32:11.401                │ │
│                 │  │ ✗ Canonical-shape FAIL: price not numeric      │ │
│                 │  │ → Quarantined  14:32:11.812                    │ │
│                 │  │                                                │ │
│                 │  │ [View bronze blob (ops)] [Open in Quarantine]  │ │
│                 │  └────────────────────────────────────────────────┘ │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `GET /audit/events` (BigQuery-backed; filter params + pagination)
- `GET /audit/trace/{trace_id}` (full lifecycle for a single trace)

**Notes:**
- Per architecture §8 + decisions.md D22, every event carries `mapping_version_id` post-mapping. Lifecycle view should display it.
- For tenant audience, audit feed is RLS-scoped (own-tenant only).

---

### Screen 9: Notifications

**Audience:** Tenant (primary).
**Route:** `/notifications` (or modal/drawer from bell icon).
**Purpose:** In-app surface for tenant alerts: quarantined rows, shadow rollout ready, mapping promoted, errors persistently high.

**Key data displayed:**
- Notification list: severity (info / warning / error), title, summary, timestamp, link
- Filter: unread / all / by type

**Key actions:**
- Click notification → navigate to related surface
- Mark read / unread
- Mark all read

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Acme Retail                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Notifications                       3 unread       │
│                 │                                                     │
│                 │  Filter: [Unread] [All] [Errors only]               │
│                 │                                                     │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ ⚠ 32 rows quarantined · Shopify · 4m ago   [→] │ │
│                 │  ├────────────────────────────────────────────────┤ │
│                 │  │ ✓ Mapping v3 promoted · POS-Main · 2h ago  [→] │ │
│                 │  ├────────────────────────────────────────────────┤ │
│                 │  │ ⚠ Shadow output ready · ERP-Daily · 6h ago [→] │ │
│                 │  ├────────────────────────────────────────────────┤ │
│                 │  │ ℹ Source connected · Square-NY · 1d ago        │ │
│                 │  └────────────────────────────────────────────────┘ │
│                 │                                                     │
│                 │  [Mark all read]                                    │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `GET /notifications` (per-user, scoped)
- `PATCH /notifications/{id}/read`
- `POST /notifications/mark-all-read`

**Notes:**
- Architecture §2.6 implies notifications exist but doesn't spec the surface. v1.0 minimum is in-app only; email/Slack delivery can be deferred.
- Notification triggers (from dis-api or a notification-emitter service):
  - Row quarantined (batched: "X rows quarantined in last Y minutes")
  - Shadow rollout ready for review
  - Mapping promoted to active
  - Mapping deprecated
  - Source health degraded (e.g., >10% quarantine rate for 1h)

---

### Screen 10: Ops Fleet

**Audience:** Ops only.
**Route:** `/ops/fleet`.
**Purpose:** Cross-tenant health overview. "Which tenants need attention right now?"

**Key data displayed:**
- Tenant list with health badges, last activity, quarantine rate, source count
- Sortable by quarantine rate (descending = problem tenants first)
- Filter by source type, tenant tier (paid / free), region
- Roll-up stats: total tenants, total sources, total quarantine rate, p95 latency

**Key actions:**
- Click tenant row → switch context to that tenant's surfaces (Quarantine, Audit, Sources)
- "Send notification to tenant" (e.g., manual alert about a known issue)
- View tenant's Mapping Versions & CRUD

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Ops Console                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  Ops Fleet                                          │
│ (ops nav)       │                                                     │
│                 │  ┌─ Rollup ──────────────────────────────────────┐  │
│                 │  │ 12 tenants · 47 sources · 148.2k rows/24h     │  │
│                 │  │ Overall quarantine rate: 0.8%                 │  │
│                 │  │ p95 latency: 4.2s                             │  │
│                 │  └───────────────────────────────────────────────┘  │
│                 │                                                     │
│                 │  Filter: [Tier ▼] [Region ▼] [Sort: Q% desc ▼]      │
│                 │                                                     │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ Tenant          Sources Active  Q% 24h Status  │ │
│                 │  ├────────────────────────────────────────────────┤ │
│                 │  │ Acme Retail     4       4       8.2%   ⚠ High │ │
│                 │  │ Gamma Stores    3       3       3.1%   ⚠       │ │
│                 │  │ Beta Stores     5       5       0.4%   ● OK    │ │
│                 │  │ Delta Foods     2       2       0%     ● OK    │ │
│                 │  │ ... (8 more)                                   │ │
│                 │  └────────────────────────────────────────────────┘ │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `GET /ops/fleet/summary` (rollup stats)
- `GET /ops/fleet/tenants` (tenant-level health table)
- `POST /ops/tenants/{id}/notify` (manual notification trigger)

**Notes:**
- This is the closest thing in v1.0 DIS to a "system observability" surface for ops. Could grow to include DLQ depth, mapping refresh lag, etc. in v2.
- Tenant click navigates to tenant's surfaces (Quarantine, Sources, etc.) with ops privilege intact (cross-tenant scope).

---

### Screen 11: DuckDB Query Panel

**Audience:** Ops only.
**Route:** `/ops/duckdb`.
**Purpose:** Ad-hoc SQL over GCS bronze blobs. Debug specific rows without spinning up streaming consumer or BQ.

**Key data displayed:**
- GCS URI input
- SQL query textarea
- Query history (last 10 queries by this ops user)
- Results panel (table view)

**Key actions:**
- Paste GCS URI + SQL → execute
- Save query to history
- Export results as CSV

**Wireframe:**

```
┌───────────────────────────────────────────────────────────────────────┐
│ DIS · Ops Console                                    [bell] [profile] │
├───────────────────────────────────────────────────────────────────────┤
│ Sidebar         │  DuckDB Query Panel (ops only)                      │
│ (ops nav)       │                                                     │
│                 │  GCS URI: [gs://ithina-bronze/2026-05-28/______]    │
│                 │                                                     │
│                 │  SQL:                                               │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ SELECT COUNT(*), MIN(txn_date), MAX(txn_date)  │ │
│                 │  │ FROM read_csv('gcs://...')                     │ │
│                 │  │ WHERE store_id = 'STORE-01'                    │ │
│                 │  └────────────────────────────────────────────────┘ │
│                 │                                                     │
│                 │  [Execute]              [Recent queries ▼]          │
│                 │                                                     │
│                 │  Results: 1 row                                     │
│                 │  ┌────────────────────────────────────────────────┐ │
│                 │  │ count   min_date     max_date                  │ │
│                 │  ├────────────────────────────────────────────────┤ │
│                 │  │ 14,802  2026-05-27   2026-05-28                │ │
│                 │  └────────────────────────────────────────────────┘ │
│                 │                                                     │
│                 │  [Export CSV]                                       │
└─────────────────┴───────────────────────────────────────────────────┘
```

**dis-api dependencies:**
- `POST /ops/duckdb/query` (runs query against GCS via DuckDB; returns results)
- `GET /ops/duckdb/history` (per-user query history)

**Notes:**
- Per architecture §4.14, DuckDB is recommended-but-optional. If skipped in v1.0, this screen is also skipped; debugging uses BQ manual loads instead.
- Strict ops-only gating; tenant should not have access (raw bronze contains tenant data they may not own conceptually + this surface is meant for debugging, not workflow).

---

## 6. Cross-cutting concerns

### 6.1 Navigation structure

**Tenant sidebar (default after auth for tenant role):**

```
┌────────────────────────┐
│ DIS · Acme Retail      │
├────────────────────────┤
│ [*] Dashboard          │
│ [-] Sources            │
│ [-] Upload             │
│ [-] Quarantine  (32)   │
│ [-] Audit              │
│ [-] Notifications (3)  │
└────────────────────────┘
```

**Ops sidebar (PLATFORM role):**

```
┌────────────────────────┐
│ DIS · Ops Console      │
├────────────────────────┤
│ [*] Fleet              │
│ [-] All tenants        │
│ [-] All quarantine     │
│ [-] Audit (cross-tnt)  │
│ [-] DuckDB             │
├────────────────────────┤
│ Switch to tenant view: │
│ [Acme Retail ▼]        │
└────────────────────────┘
```

**Notes:**
- Ops can drop into any tenant's view (similar to admin-frontend's Anjali viewing Żabka). Tenant switch in sidebar.
- "Notifications (3)" badge for tenant updates in real-time (poll or WebSocket).
- "Quarantine (32)" badge same pattern.

### 6.2 Auth + RBAC integration

**Today (v0 dev):** JWT-based, same pattern as admin-frontend stub mode.
- Persona JWTs for testing: tenant-persona JWT (e.g., "Acme Retail operator"), ops-persona JWT (e.g., "Anjali PLATFORM").
- AuthBoundary pattern: validates JWT, populates AuthSnapshot, gates rendering.
- `userType` claim: TENANT or PLATFORM.
- `tenant_id` claim: scopes data; PLATFORM users can spoof via "switch tenant" dropdown.

**Future (Customer Master Auth0):**
- Customer Master issues real JWTs after Auth0 login flow.
- dis-api validates Customer Master tokens (per decisions.md D25).
- RBAC claims from Customer Master:
  - `userType` (PLATFORM / TENANT)
  - `tenant_id` (for TENANT users)
  - `permissions` (RBAC tuples; same shape as admin-frontend's ADMIN.AUDIT_LOG.VIEW.TENANT-style)
- Migration path: replace stub-mode JWT generation with Customer Master OIDC flow; AuthBoundary logic stays.

**RBAC by surface:**

| Surface | TENANT | PLATFORM |
|---|---|---|
| Tenant Dashboard | ✓ own | ✓ via switch |
| Sources & Connections | ✓ own | ✓ cross-tenant |
| Sample Upload | ✓ | ✓ |
| Mapping Review | ✓ | ✓ |
| Shadow Rollout Review | ✓ | ✓ |
| Mapping Versions & CRUD | ✓ view; edit limited | ✓ full edit |
| Quarantine Console | ✓ tenant slice | ✓ ops slice |
| Audit & Trace Lookup | ✓ own | ✓ cross-tenant |
| Notifications | ✓ | ✓ (own ops notifs) |
| Ops Fleet | ✗ | ✓ |
| DuckDB Query Panel | ✗ | ✓ |

### 6.3 Mobile / responsive

**v1.0 stance:** Desktop-first. DIS UI is operational tooling; primary usage is workstation-bound (data-team users at desktops). Mobile responsiveness is best-effort but not a launch criterion.

**Critical surfaces that should still work on mobile:**
- Tenant Dashboard (status check)
- Notifications (alert dismissal)
- Quarantine Console (list view; detail OK to require desktop)

**Surfaces that are fine desktop-only:**
- Mapping Review (column-wise table is dense)
- Shadow Rollout Review
- DuckDB Query Panel (multi-line SQL editor)
- Audit & Trace Lookup (detailed lifecycle view)

### 6.4 Error states + empty states

Every screen needs:
- **Loading state:** skeleton or spinner. Don't show empty data as "no data" while loading.
- **Empty state:** clear message + CTA. E.g., "No sources yet. [+ Add your first source]"
- **Error state:** user-readable error + retry. Avoid leaking backend error codes.
- **Permission-denied state:** "You don't have access to this surface" with link back.

### 6.5 Notifications + real-time updates

**v1.0 polling-based:**
- Notification bell polls `/notifications/unread-count` every 30s.
- Quarantine list polls every 60s when active.
- Dashboard auto-refreshes every 60s.

**v2 WebSocket / SSE:**
- Real-time notification push.
- Quarantine list updates as new rows arrive.
- Trace lookup live-tails when investigating ongoing issues.

---

## 7. Sequencing recommendation

If building DIS UI in `ithina-dis/ui/`, recommend phasing roughly as:

**Phase 1 (MVP, demonstrable end-to-end):**
- Auth (JWT stub mode, mirrors admin-frontend)
- Sample Upload
- Mapping Review
- Mapping Versions & CRUD (read-only list view)
- Quarantine Console (tenant slice only)
- Audit & Trace Lookup (trace_id direct lookup only)

**Phase 2 (tenant complete):**
- Tenant Dashboard
- Sources & Connections (full CRUD)
- Shadow Rollout Review
- Notifications
- Quarantine Console (resubmit actions)

**Phase 3 (ops surfaces):**
- Ops Fleet
- Quarantine Console (ops slice + ops actions)
- Audit & Trace Lookup (cross-tenant + filters)
- DuckDB Query Panel

**Phase 4 (Customer Master Auth0 integration):**
- Replace JWT stub mode with Customer Master OIDC flow.
- Wire RBAC claims from Customer Master into AuthBoundary.

**Honest scoping notes:**
- Phase 1 gives a working end-to-end onboarding + quarantine flow for a single tenant. Enough to validate the architecture.
- Phase 2 makes it production-acceptable for tenant operators.
- Phase 3 unblocks Ithina ops at scale.
- Phase 4 is the auth migration; technically independent of feature work.

---

## 8. Open questions

These need product/design input before the ithina-dis/ui/ build starts.

**Q1: Single tenant onboarding vs multi-source onboarding flow.** Does one "Sample Upload" trigger one source registration, or can a tenant onboard multiple sources from one CSV? v1.0 assumes one source per upload; revisit when tenants ask for batch.

**Q2: Self-serve vs ops-driven default.** Architecture §5.4 says both are supported. Does v1.0 ship self-serve as default, or ops-driven as default with self-serve as opt-in? Affects landing-page UX.

**Q3: Notification delivery channels.** v1.0 is in-app only. When do email/Slack/webhook deliveries get added? Affects notification trigger backend design.

**Q4: Tenant tiers (free / paid).** Does Customer Master expose tenant tier? Some surfaces (e.g., DuckDB query panel for tenants? larger upload size limit?) may be tier-gated.

**Q5: Multi-store tenants.** Many tenants will have 25+ stores. Do sources scope to all-stores or per-store? Sources & Connections wireframe shows per-store association; verify with Sanjeev.

**Q6: Mapping ownership.** When a mapping version is edited, who owns the new version - the tenant operator who edited, or Ithina ops who promoted? Audit trail needs to capture both.

**Q7: Shadow rollout window default.** How long does shadow run before review? 1 hour? 24 hours? Until N chunks accumulate? Operator choice or fixed?

---

## 9. Surfaces explicitly out of scope for v1.0

These exist in Sanjeev's architecture but don't have dedicated screens in v1.0:

- **Schema CRUD (canonical schema admin).** Per repo-structure.md, this lives in dis-api but is a Sanjeev/Ithina ops concern, not surfaced as a tenant-facing UI. Defer to v2 (or never, if it stays a code-level concern).
- **Identity Service management.** Tenant + store identity is managed in Customer Master, not DIS UI.
- **Pipeline runtime observability.** SLO dashboards, DLQ depth charts, throughput graphs. Belongs in Grafana / Cloud Logging dashboards, not DIS UI.
- **Cost dashboards.** Per cost-estimate.md, infra cost monitoring is GCP-native, not DIS UI.
- **Validation rule CRUD (independent of mapping).** Validation suites are tied to mapping versions per decisions.md D4a. Editing a validation suite means editing the mapping; no standalone validation surface.

---

## 10. Mining patterns from archive/dis-legacy/

The previous DIS UI built inside admin-frontend (archived in admin-frontend's `archive/dis-legacy/`) has UX patterns worth reusing:

**Bucket A (directly reusable):**
- **Uploads workflow** (`components/dis/uploads/`): Drop zone, sample row preview, column mapping review. Direct match for Screens 3 + 4.
- **Canonical schema CRUD** (`app/(dis-authenticated)/dis/canonical-schema/`): Domain → entity → field tree, per-field metadata, version bumping. Reusable for Screen 6 if scope expands.

**Bucket B (concept reusable):**
- **5-step wizard pattern** (`components/dis/sources/AddSourceWizard.tsx`): Type → Org Node → Name → Config → Test. Adapt for Screen 3 + 4 onboarding flow.
- **Status chips** (`components/dis/chips/`): 19 status chips. Recipe transferable; per-chip semantics need re-mapping to dis-api vocabulary.
- **Run detail header + error panel** (`components/dis/runs/`): Closest existing UX for Audit Lookup detail view (Screen 8).
- **FleetPageShell** (`components/dis/shared/`): Page wrapper. Trivially portable.

**Bucket C (no direct parallel; reference only):**
- Backfills, Alerts, Freshness, LLM Ops surfaces. Not in v1.0 Sanjeev scope. Reference if v2 expands.

See `archive/dis-legacy/ARCHIVE_NOTE.md` in admin-frontend for full bucket mapping.

---

## 11. Revision history

- **v1.0 (2026-06-02).** Initial draft. Based on Sanjeev's locked DIS architecture (architecture.md, decisions.md, build-guide.md, repo-structure.md, engineering-reference.md, cost-estimate.md). 11 screens across 2 audiences; phased build sequencing; 7 open questions surfaced.
