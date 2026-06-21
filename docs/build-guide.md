# DIS Build Guide

**Purpose.** Top-level operator dashboard. Ordered work items grouped by phase. Sequence reflects real dependencies, not a schedule. Each item carries a status and a one-line description of what is implemented when DONE.

**Status values.** `TODO` (not started), `WIP` (in progress), `DONE` (merged), `DEFERRED` (intentionally not in v1.0; trigger named).

**How to update.** Edit by hand. Change the status word in-place when an item moves.

**Companion docs.**
- `architecture.md` — system rationale.
- `decisions.md` — indexed decision register.
- `engineering-reference.md` — top-level repo index.
- `repo-structure.md` — detailed directory trees.
- `cost-estimate.md` — beta-scale projection.
- `local-setup.md` — devbox setup.

---

## Phase 0: Foundation

Interface-freezing. Until done, no service implementation can be truly parallel.

### Workspace and tooling

- `DONE` Workspace dependencies declared and installed; any developer running `uv sync` gets the same 151 packages.
- `DONE` Python 3.12 pinned project-wide via uv.
- `DONE` `make` targets cover the daily commands: stack up/down/reset, db migrate, tests, lint, format, topic creation, dbt run.
- `DONE` Local env vars centralized in `.env` (gitignored) and `.env.example` (committed).
- `DONE` `pre-commit` hooks block bad commits at commit-time: lint, format, whitespace, EOF, YAML/JSON syntax, large files.
- `DONE` `scripts/check_setup.sh` runs six tiers of preflight checks and reports PASS / FAIL / SKIP with hints.

### Local stack

- `DONE` Four containers run from one `docker-compose.yml`: Postgres on 5433, Pub/Sub emulator, fake-gcs-server, Redis.
- `DONE` Postgres bootstraps with two roles: `ithina_dis_admin` (for migrations) and `ithina_dis_user` (NOSUPERUSER NOBYPASSRLS for service code). RLS posture matches production from day one.
- `DONE` All six DIS Pub/Sub topics are created automatically on `make run-local`.
- `DONE` Smoke tests against the running stack pass: Postgres accepts queries, topics list, GCS responds, Redis pings.

### Schemas

- `DONE` Postgres DDL for every DIS schema (canonical, bronze, config, identity_mirror, quarantine, staging, plus UUIDv7 extension) lives at `schemas/postgres/<schema>/`.
- `DONE` BigQuery DDL for `canonical_history.*` and `audit_events` lives at `schemas/bigquery/`.

### Migrations and analytics tooling

- `DONE` Alembic is wired to the admin Postgres role and ready to run migrations.
- `DONE` dbt is wired to BigQuery via OAuth and ready to run models.

### Contracts

- `DONE` Six Pub/Sub message schemas (`ingress.ready`, `ingress.resubmit`, `identity.changed`, `quarantine`, `mapping.changed`, `pipeline.dlq`) frozen with examples.
- `DONE` Identity Service contract frozen: OpenAPI (authoritative), proto (reference), attribute-needs and decisions docs.
- `TODO` Customer Master contract written down and signed off by the Customer Master team, capturing what DIS depends on (JWT shape, upload session lifecycle, identity change events).

### Service and lib scaffolding

- `DONE` All 11 service directories exist with their own `CLAUDE.md` (rules) and `README.md` (EPE block).
- `DONE` All 9 lib directories exist with their own `CLAUDE.md` and `README.md`.
- `DONE` Placeholder stub modules for the test fakes exist so `.env` references aren't dangling.

### Documentation

- `DONE` Root `CLAUDE.md` and `README.md` published; Claude Code auto-loads `CLAUDE.md` at session start.
- `DONE` All design docs in `docs/`: architecture (md + html), decisions, engineering-reference, repo-structure, build-guide, cost-estimate, local-setup, worked-example.

### Git and CI

- `DONE` Local git initialized on `main`; root commit captures verified Phase 0 state.
- `TODO` GitHub repo created, code pushed; team can clone and `make run-local`.
- `TODO` Every push and PR triggers ruff lint, ruff format check, and pytest collection on GitHub Actions.

### Infrastructure (deferred until needed)

- `DEFERRED` Terraform applies cleanly to `ithina-dis-dev` GCP project. *Trigger: first slice that needs a real cloud resource.*
- `DEFERRED` `ithina-dis-dev` GCP project created and billed. *Trigger: same as Terraform.*
- `DEFERRED` `ithina-dis-staging` GCP project created. *Trigger: v1.0 launch readiness review begins.*
- `DEFERRED` `ithina-dis-prod` GCP project created. *Trigger: tenant onboarding date confirmed.*

**Phase 0 exit criterion.** Every non-DEFERRED item above is DONE. `check_setup.sh` is green. `ithina_dis_user` can query empty DIS tables; the system is ready for Slice 1.

---

## Phase 1: Slice-driven service implementation

Each slice is one end-to-end vertical cut. Slice docs live in `docs/slices/slice-NN-<short-name>.md`. The slice doc is the source of truth Claude Code reads.

### Bootstrap

- `DONE` Slice 1: Bootstrap Alembic migration (`alembic/versions/0001_bootstrap.py`). Applying migrations creates every DIS schema (canonical, bronze, config, identity_mirror, quarantine, staging, audit, plus the `public.uuidv7()` function) in local Postgres; `\dn` lists them. The migration is a manifest that applies the `schemas/postgres/` DDL files verbatim in dependency order, then authors the 7 `CREATE SCHEMA`s, a centralized grants block, and 7 daily partitions for each of the 7 partitioned parents (canonical/staging event + signal_history tables, and `audit.events`). `ithina_dis_user` gets USAGE on schemas + table SELECT/INSERT/UPDATE/DELETE + sequence USAGE (incl. `ALTER DEFAULT PRIVILEGES` so later partitions inherit) so service code can read/write but cannot bypass RLS. `env.py` resolves the connection from `POSTGRES_ADMIN_URL` (no hardcoded host/port/role) and a `current_database()` guard refuses to run against the wrong DB (protects Customer Master on 5432).

  **Cloud bootstrap run-order** (Cloud SQL; run once per fresh instance, the role step is NOT part of the migration): (1) run `schemas/postgres/00_bootstrap/roles.sql` as the Cloud SQL admin against the DIS database, passing `-v dis_admin_password=... -v dis_user_password=...`; (2) export `POSTGRES_ADMIN_URL` pointing at the DIS database as `ithina_dis_admin`; (3) `alembic upgrade head`. Locally these roles come from docker-compose + `infra/local/postgres-init.sql` instead.

### Test infrastructure

- `DONE` Slice 2: Identity Service and Customer Master fakes, plus a test fixture seeder. Customer Master fake issues signed test JWTs and publishes a JWKS endpoint for verification; serves upload sessions; emits `identity.changed` Pub/Sub events when tenants/stores are seeded or changed. Identity Service fake answers all four methods (`resolve_from_token`, `resolve_from_upload`, `resolve_from_endpoint`, `validate`) with canned data. Fixture seeder is **for tests only**: writes test tenants/stores into `identity_mirror`, plus a default test `config.source_mappings` row, bypassing the CM-DB sync of Slice 7 so tests don't need a running Customer Master. Devbox runtime uses Slice 7 (DB-pull from real CM) for `identity_mirror`; runtime source mappings get created via Slice 14 onboarding flow or hand-crafted before Slice 19. Both fakes run as FastAPI apps in docker-compose; every later slice tests against them.

### Shared libraries

Phase 1 services depend on a set of shared libraries. Each lib slice builds the lib to the surface area current and upcoming services need; later slices may extend.

- `DONE` Slice 3: Core primitives. `libs/dis-core` gains `errors` (single `DisError` root, leaf-level; the six Slice 2 interim exceptions consolidated — identity trio moved here and re-exported by `identity/`, dis-testing's `TestInfraError` reparented onto `DisError` with no dis-core→dis-testing inversion), `identifiers` (internal UUID-key aliases; documented collision with the identity contract's external `t_*`/`s_*` aliases, the D37 split), `ids` (UUIDv7 generation), `trace_id` (mint + contextvar), `timestamps` (UTC-only), `logging` (structured JSON), and an inert Phase-1 `BqClient` stub (real client Phase 3 / Slice 21, D34). `libs/dis-canonical` provides one Pydantic model per canonical base table (`StoreSkuCurrentPosition`, `StoreSkuSaleEvent`, `StoreSkuChangeEvent`, `StoreSkuSignalHistory`), hand-aligned to the live `ithina_dis_db` schema (signal_history correctly carries no `mapping_version_id`; D22/D31/D32). A DB-backed integration test reconciles every model's fields against `information_schema.columns` (exact set match, both directions) as a drift guard. Surfaced D38 (OPEN, deadline Slice 10): the D33 dedup key names `source_id`/`source_event_id` columns absent from the applied schema. `make check` 60/60; mypy + ruff clean.
- `DONE` Slice 4: Data plane safety. `libs/dis-rls` (async RLS-aware Postgres session context manager that sets `app.tenant_id`; tests prove queries from one tenant cannot see another), `libs/dis-pii` (deterministic per-tenant HMAC tokenization function and per-tenant key handling; storage backend for the token → ciphertext mapping is deferred until a non-CSV receiver flags PII columns — see `decisions.md` D24; until then, `dis-pii` raises at startup if a source mapping flags a column as PII without a configured backend, so accidental PII landing in canonical fails loudly), `libs/dis-storage` (GCS path scheme, signed URL issuance, GCS object access).
- `DONE` Slice 5: Pipeline mechanics. `libs/dis-mapping` (four-stage mapping engine: rename, normalize, cast, derive; pure functions over `(mapping, raw_row) → canonical_row`) and `libs/dis-validation` (Pandera suite runner, pre-mapping and post-mapping shapes).
- `DONE` Slice 5b: Enrichment. `libs/dis-enrichment` (pure, consumer-executed lib mirroring `dis-mapping`: owns its registered canonical fields, its output wins over the mapping, resolving each from an authoritative internal source). Phase-1 scope: `currency` + `tax_treatment` on the snapshot / current-position path only, enrichment placed BEFORE post-validation (D94) so enriched values are canonical-shape-validated and count toward completeness; migrates the existing hardcoded `tax_treatment` injection into the lib on that path (D98). Lookup-enrichment ONLY, never computed/derived attributes (daily-compute). Registry code-owned behind a swappable seam (config table later). Later slices may extend: additional internal sources, event/history paths, conditional resolution. Decisions D94–D98 in decisions.md. TODO follow-up (build-plan): the create-time "these mapped fields are internally-sourced" response note — the create endpoint reads the enrichment registry (out of slice-5b scope; `services/dis-ui` is Amit's).
- `DONE` Slice 6: Audit. `libs/dis-audit` writes audit events to the Cloud SQL `audit.events` table (fire-and-forget; failures logged, not raised). Audit emission is service-layer, not lib-layer — libs do not emit audit events, services do (Slice 7 onward). BigQuery archival of audit events deferred to Phase 3.

### Identity mirror (so receivers and streaming consumer can FK against it)

- `DONE` Slice 7: Mirror Sync Consumer — DB-pull mode. `services/mirror-sync-consumer/` reads tenant and store records directly from Customer Master's Postgres database (port 5432 locally; Cloud SQL in cloud); upserts into `identity_mirror.tenants` and `identity_mirror.stores`. Runs on-demand for first load and is schedulable for periodic reconciliation. Same code serves local and cloud — no separate local seeder vs cloud sync. The Pub/Sub-driven incremental consumer mode is deferred to a later slice (triggered when Customer Master emits real `identity.changed` events); this slice is the v1.0 production path for both initial bulk load and ongoing reconciliation. Tests bypass this service entirely via Slice 2's fixture seeder. **Resolved (D41):** `identity_mirror` is **not** RLS-protected (RLS-off, no policies, confirmed by live introspection). Mirror Sync's upsert is a plain write as the DIS service role — no per-row `app.tenant_id` scoping and no distinct role (it flows through `dis-rls` `rls_session` only to inherit the `current_database()` target guard).

### CSV upload (v1.0)

- `DONE` Slice 9a: Identity correction (precursor to Slice 8 and 9b). Corrects the invented `t_*`/`s_*` identity form across the system so internal UUIDs are the load-bearing identity and Customer Master's `display_code`/`store_code` are the authoritative readable codes. Edits all DIS Pub/Sub contracts in place: `tenant_id`/`store_id` to UUID, optional producer-required `tenant_display_code`/`store_code`, `gcs_uri` tenant segment to the UUID, `identity.changed` payload `is_active` to `status`; adds the new `csv.received` contract (D52, D53, D54). `libs/dis-storage` path scheme and hard rule 9 move to the UUID tenant segment (D53). The Identity Service contract returns the internal UUID alongside the external codes, and the Slice 2 fake is updated to match (D37). `identity_mirror.tenants` gains `display_code` and `identity_mirror.stores` gains `store_code` (nullable, copy-as-is), reopening Slice 7's Mirror Sync to populate them (D55). The real Identity Service is Slice 13; 9a commits the contract and updates the fake now, the real implementation honors it at Slice 13.
- `DONE` Slice 9b: CSV upload, Phase 2 worker (`csv-ingest-worker`). Depends on Slice 9a. A `csv.received` event from dis-ui-server triggers `services/csv-ingest-worker/`; the worker trusts the event's resolved identity and reads `trace_id` from it, so it calls no Identity Service and mints no `trace_id` (D54). Steps: DuckDB structural preflight (structure, row count, type sniff), the `dis-pii` fail-loud gate (wired only; the CSV-flag path is inert under the current schema, D40), bronze metadata write via `libs/dis-rls`, `ingress.ready` publish, audit emission. Bronze-first: bronze lands before the publish (D5). Idempotency: same content hash + `source_payload_id` (the upload session) + tenant within 24h returns the prior `trace_id`. See D36 (Phase split) and D54 (trigger model).

### Receivers — API / webhook

- `DEFERRED` `services/receiver-api/`. Bearer-token or API-key authenticated; accepts pushed JSON payloads from tenant systems; same downstream contract as CSV upload (bronze + `ingress.ready`). *Trigger: first tenant requests API/webhook ingestion.*

### Receivers — ERP CSV POST

- `DEFERRED` `services/receiver-csv-erp/`. Per-tenant POST endpoint for ERP-driven CSV batches; per-tenant API key or mTLS auth; identity bound to endpoint config. *Trigger: first tenant requests ERP POST endpoint.*

### Receivers — Reverse-API pull

- `DEFERRED` `services/receiver-reverse-api/`. Cursor-based puller from external APIs; identity bound to endpoint config registered for that pull target. *Trigger: first tenant requests reverse-API pull.*

### Streaming pipeline

- `DONE` Slice 10: Streaming consumer happy path. Reads `ingress.ready`, fetches bronze chunk from GCS, applies a stub mapping, validates with Pandera, writes the canonical hot table plus the event table in a single transaction, emits audit events. FK to `identity_mirror` enforced; RLS enforcement verified.
Deferred at Slice 10, reactive-only by design: the named-custom-transform escape hatch (D61) and proactive schema-drift monitoring (D62). Triggers recorded in decisions.md.

- `DONE` Slice 11a: Quarantine the storm class (direct write) — D82. The streaming consumer recognizes a known-deterministic failure (the D82 narrow allowlist: `MAPPING_CONFIG_INVALID` — the storm's exact cause — `SUITE_REF_UNSUPPORTED`, guarded `CONTRACT_VIOLATION`; gate failures as `VALIDATION_ROW_FAILED`) and writes it DIRECTLY to the `quarantine.*` tables (chunk-level to `quarantined_chunks`, row-level to `quarantined_rows`, status=NEW), emits the reserved `QUARANTINED` audit stage carrying the D78 failure-audit shape (Outcome.SUCCESS, the disposition record), and ACKS the message so it stops redelivering, breaking the storm at its source. `libs/dis-quarantine` owns the fail-loud write path (reusable; the worker adopts it later); a failed hold NACKS, never ack-and-lose. The self-heal cases (`HOT_POSITION_MISSING`, the store-miss contract violation) are deliberately EXCLUDED and keep nacking (the D82 governing principle: retry is their designed recovery until replay exists), with the Pub/Sub dead-letter policy as the infra backstop. Zero schema change. Stopped the storm; did not build replay, the drainer, or broad classification. *Lights up Amit's Quarantine console with real held data.*
- `TODO` Slice 11b: Quarantine topic + drainer (the decoupled path). The topic-mediated design the original Slice 11 described: failing rows flow to the `quarantine` topic, and a drainer service consumes it (and the Pub/Sub dead-letter topics) and writes the `quarantine.*` tables, decoupling the consumer from the store and processing the DLQ backstop's contents. Supersedes 11a's direct write where the decoupling is wanted. *Trigger: after 11a; when the DLQ backstop accumulates contents that need draining, or the consumer-coupling becomes a constraint.*

- `TODO` Slice 12: Replay tooling. `tools/replay/` CLI lets an ops operator replay a bronze chunk; the replay gets a new `trace_id` linked to the original as `parent_trace_id`; audit records the chain. The dis-ui-server replay endpoint (UI-driven resubmit) is built in Slice 13 as a thin wrapper over this tooling.

### dis-ui-server + Identity Service real

- `DONE` Slice 13a: dis-ui-server foundation. Deployable FastAPI service skeleton: `main.py` app + `config.py`, `/healthz` (DB-free liveness) and `/readyz` (readiness proves a tenant-scoped session opens), the `dis-core` error to HTTP exception handlers + error envelope, structured logging, and `libs/dis-rls` per-tenant session wiring against the DIS database. The auth seam lands here as a single FastAPI dependency (`get_current_identity` + `require_tenant`/`require_ops`) backed by a dev-stub verifier, and is the sole source of `tenant_id` that feeds the session (never body/query/unverified header), so the RLS / NOBYPASSRLS / zero-cross-tenant-leakage rule holds from day one. No UI data endpoints; later slices hang handlers off this base. ORM (SQLAlchemy declarative) is introduced here for this service's CRUD/system-of-record nature, executed only through the `dis-rls` session, not a separate engine. Customer Master replica reads and the `dis-rls` PLATFORM/cross-tenant variant are out of scope (Slice 13b / first ops-read slice). Exit: the service builds, runs as a container, answers `/healthz`, and `/readyz` proves a tenant-scoped session opens.
- `TODO` Slice 13b: Identity Service real implementation. The four methods (`resolve_from_token`, `resolve_from_upload`, `resolve_from_endpoint`, `validate`) work against an in-process cache backed by Customer Master with stale-while-error fallback to `identity_mirror` (D28). dis-ui-server consumes it via the same client interface its tests use against the Slice 2 fake, replacing the stub seam introduced where identity is first needed (Slice 8). Real Customer Master JWKS auth (D25) replaces the 13a dev-stub verifier here.
- `DONE` Slice 14a: `config.source_mappings` template grain + RLS ON. Schema migration only: the mapping grain becomes `(tenant_id, source_id, template_id)` so one source carries multiple named templates (D68); the table moves from RLS-OFF to RLS ON+FORCE with the single-GUC `app.tenant_id` `tenant_isolation` policy, correcting the stale "configuration, not tenant data" comment (D69). `template_name` is unique per `(tenant, source)` among non-deprecated rows via an EXCLUDE constraint (adds `btree_gist`); `config.source_mappings_v` set `security_invoker=true` to close an owner-rights RLS bypass; per-template version-seq and active-uniqueness rekeyed. One delta migration `0005` plus the matching manifest, converging delta and fresh-bootstrap paths; test-infra (seeder/fixtures/consumer conftest) updated as the RLS-ON blast radius.
- `DONE` Slice 14b: mapping + store data endpoints (the first dis-ui-server data slice; sets the API pattern). Five endpoints under `/api/v1`: `GET /stores-onboarded` (tenant's stores from `identity_mirror.stores`, tenant from token, in-query scoping since the mirror is RLS-OFF — registered weak link, D70), `GET /template-mapping-fields` (the canonical mappable-field catalog, derived from the two event models with a boot-failing drift guard, not tenant-scoped), `GET /mapping-templates` + `/{template_id}` (list/detail via `rls_session`), `POST /mapping-templates` (create a DRAFT, hand-authored rules, EXCLUDE→409), `PATCH /mapping-templates/{template_id}` (DRAFT-in-place vs new-version-with-predecessor per the D17 lifecycle; never mints a second ACTIVE). Establishes the reusable pattern: SQLAlchemy declarative models executed Core-style on the `rls_session` connection (never `AsyncSession`), the §2.3 error envelope, raw D49 `mapping_rules` on the wire, 404-throw-style lookups. Sample-driven inference and promote/reject/shadow are out of scope (the latter is the prerequisite for any second ACTIVE template).
- `DONE` Slice 14c: CORS for the browser-served `dis-ui` SPA. Config-driven `CORSMiddleware` on dis-ui-server allowing the confirmed `dis-ui` dev origin (`http://localhost:5173`), `allow_credentials=False` (Bearer header, no cookies), resolved at app-build time for middleware-registration timing. No wildcard; environment-driven; unblocks the browser path only (curl never needed CORS). Server-side only; `services/dis-ui` untouched.
- `DONE` Slice 14d: the catalogue ingestion front door. A stored `template_type` discriminator on `config.source_mappings` (delta migration `0010` + matching manifest, `TEXT COLLATE "C" NOT NULL`, no DB enum/CHECK — the vocabulary lives once in `dis_validation.TEMPLATE_TYPES`) formalises the implicit sale-vs-change inference into a stored column read by the field catalog, the rule-target validator, and the streaming consumer's routing; backfilled from the rule-target signature (legacy/empty → `sales`), converging delta and fresh-bootstrap paths. `GET /template-mapping-fields` becomes TYPE-AWARE — one field set per `template_type` (`sales`/`inventory_change`/`snapshot`), the `snapshot` (catalogue / current-position) set routing directly into the hot table beside the event packets — and the consumer routes a snapshot template to `StoreSkuCurrentPosition` (the catalogue write path). Catalogue revision: `store_code` removed from the catalog (store identity is receiver-resolved, D86); `__ignore__` offered on all three sets with source-column-assignment enforcement deferred (D87). The dis-validation optional-decimal canonical-shape fix rides along (shared lib; the event path also runs it).
- `TODO` Slice 14: Onboarding — sample-driven mapping assist (the remaining work under this banner). dis-ui-server's onboarding sub-module takes a sample upload and produces a draft mapping (rule-based schema inference + suggestions) that pre-fills the hand-authored create flow built in 14b; operator reviews and promotes to active; new tenant CSV onboards end-to-end without manual SQL. The schema (14a), the mapping/store endpoints (14b), and the browser CORS bridge (14c) are done; what remains is the sample-upload + inference + suggestion layer and the promote-to-active path. Depends on the promote/reject/shadow slice (for the active transition) and Slice 8 (for the sample upload mechanics).
- `TODO` Slice 15: dis-ui-server endpoints — group 1. *Placeholder; scope drafted from UI engineer's demand list. Endpoints land here as a coherent feature group (e.g., dashboards, history views, ops surfaces).*
- `DONE` Slice 16a: create-template request contract redesign (request shape only). `POST /api/v1/mapping-templates` now carries semantic intent per column — `columns: [{src_key, dest_key, + source-format declarations (src_datetime_format / src_decimal_separator / src_thousand_separator / src_is_percentage)}]` — instead of a pre-assembled D49 `mapping_rules` document; the backend re-derives every catalog/sink fact from `template_type` + `dest_key`, so the request never echoes the sink object (D89). The endpoint shape-validates ONLY (strict `extra="forbid"` body + column models, `template_type` vocab → 400, `columns` non-empty, format declarations well-formed-if-present) and returns 201 with a SYNTHETIC `MappingTemplateDetail` (real-but-non-persisted UUIDv7, DRAFT, empty `mapping_rules`, `mapping_version_id=0`); it does NOT persist to `config.source_mappings` and does NOT assemble `mapping_rules` — translation + the DB write land in 16c, new ops in 16b. Endpoint path unchanged (verb-rename dropped, D89). GET/list/PATCH endpoints and handlers untouched; create-persistence tests skip-marked "restore in 16c", read/patch tests re-seed via a direct admin INSERT; `services/dis-ui` untouched.
- `DONE` Slice 16b: `parse_percent` normalize op + `src_thousand_separator` enum widen. Added `parse_percent` to the `dis-mapping` bounded vocabulary (strips optional `%`, parses the body under declared separators, divides by 100 **losslessly via `pl.Decimal`**, never float); widened `MappingColumn.src_thousand_separator` to `{".",",","'"}` so EU `1.234,56` is declarable. Both inert until 16c wires the translator. No D-number. Scale caveat surfaced for 16c: `expiry_confidence(3,2)` and any percent with >4 fractional digits exceed canonical column scale — no schema change here.
- `DONE` Slice 16c: translation layer + ACTIVE write behind the 16a contract. The create handler translates `columns + declarations` + `template_type` into a `mapping_rules` `SourceMapping`, runs the full semantic gate (target legality, mandatory coverage, presence pairings), and on success writes a single ACTIVE row (D88 create-as-ACTIVE) — no DRAFT, no staging. Any failure (unknown date token, bad dest_key, missing mandatory, broken pairing) is a 4xx with nothing persisted. The 5 create-persistence tests skip-marked in 16a are restored as ACTIVE-write; the 16a no-write test is inverted to the live no-write-on-failure guarantee. `services/dis-ui` untouched. See the slice doc for internals.
- `TODO` Slice 16d: dis-ui-server endpoints — group 2. *Placeholder; scope drafted from UI engineer's demand list.*
- `DONE` Slice 16f: CSV delimiter detection (worker detects → envelope → consumer uses). Fixes a verified split-brain: the csv-ingest-worker's DuckDB preflight auto-detected the delimiter but discarded it while the streaming-consumer re-read with a hardcoded comma, so a non-comma file (semicolon/tab/pipe) passed preflight then mis-parsed into one mega-column downstream. The worker now captures `sniff_csv`'s `Delimiter` and stamps it on `ingress.ready` on BOTH publish paths (fresh, and the D59 resume-and-mark re-derives it from the same bytes — never a default-comma shortcut); the consumer passes `separator=event.delimiter` to `pl.read_csv`. Additive optional field (`string`, single-char, default `","`) on both `ingress.ready` and `csv.received` schemas+models — backward-compatible for in-flight/replayed messages; quoted-embedded-delimiter handled by Polars' default `"` quoting (verified, not built). `csv.received` carries the field for contract symmetry but is NOT populated (the worker is the single detector; no `services/dis-ui` edit). No D-number — aligns with the existing D54 trust-boundary model. See the slice doc for the adversarial mutation coverage (the three wiring escapes closed).
- `DONE` Slice 16g: completes 16f's `csv.received` contract symmetry. 16f added the optional `delimiter` field to the `csv.received` schema and to the worker + streaming-consumer models, but left the dis-ui-server PRODUCER model (`CsvReceivedEnvelope`, `services/dis-ui-server/src/dis_ui_server/publisher.py`) without it — so the schema gained a property the producer model lacked, and the producer-side drift guard (`test_csv_received_envelope::test_model_fields_match_contract_properties_exactly`, hard rule 10) went red. 16g adds the same additive field to the model (`delimiter: str = Field(default=",", min_length=1, max_length=1)`) — matching the worker's field exactly. Model-optional (defaulted), so it clears the field-set guard while staying out of the model-required set (`delimiter` is not in the schema `required` array — the not-in-required test still holds). `build_csv_received` unchanged: the field is defaulted and dis-ui-server still does NOT populate it (the worker's preflight remains the single detector — 16f's Approach B stands), but `to_bytes`' `exclude_none` always emits the `","` default on the wire, schema-valid. No D-number, no contract-shape change — the schema was already correct; this realigns the one model that 16f missed.
- `TODO` Slice 16h: write-gate derivation refactor (no behavior change). The streaming consumer's hardcoded `HOT_REQUIRED_FROM_PROJECTION` literal is removed; the write-time completeness gate derives its required set from the canonical model (`required ∩ mapping-produced`, enrichment-produced columns excluded from the demand) the same way the create-time gate already does. The `mandatory_mapping_produced` helper is promoted from dis-ui-server into the shared dis-validation lib and imported by both services so there is one derivation. Pinned to the hot model (`StoreSkuCurrentPosition`) regardless of the routed target, and the enrichment-fields union is preserved so currency stays satisfied (D95). Produces identical COMPLETE/INCOMPLETE verdicts to the old literal (proven no-op); the payoff is that any future NOT NULL ↔ NULLABLE change becomes a model edit with no gate code change. Own D-number at commit.
- `TODO` Slice 16i: mandatory-flag correctness (create gate + field catalog). The create-time mandatory coverage and the `GET /template-mapping-fields` catalog flag stop treating enrichment-produced columns as must-map: `currency` and `tax_treatment` become `mandatory=false` because enrichment supplies their value, not the mapping (closes the create-gate asymmetry deliberately deferred from Slice 5b / D95). Behavior change: the create endpoint stops 400-ing when a template omits an enrichment-supplied column, and the UI stops forcing those fields to be mapped. `services/dis-ui` untouched (it reads the flag from the API; behavior shift only). Own D-number at commit.
- `TODO` Slice 16j: nullable canonical columns `unit_cost` + `product_category` on `store_sku_current_position`. Migration `0012` drops NOT NULL on both (the NULL-safe `ck_sscp_unit_cost_non_negative` CHECK is kept); the dis-canonical model marks both `| None = None` (coupled to the migration by the dis-canonical rule); the `schemas/postgres/` DDL reference is synced (fresh == migrated). With 16h and 16i in place, both gates and the catalog flag auto-follow with no further code change; only the pinned-set tests (`test_field_catalog`, `test_mapping_validation`) and any dbt not-null assumption update. More permissive ingest: a snapshot row upserts even when these two are empty, downstream decides. Own D-number at commit.
- `TODO` Slice 17: dis-ui-server endpoints — group 3. *Placeholder; scope drafted from UI engineer's demand list.*
- `DONE` Slice 17b: two-GUC platform/tenant RLS (realizes D76). All 13 tenant-scoped policies become asymmetric two-GUC — USING widens to `OR current_setting('app.user_type',true)='PLATFORM'`, WITH CHECK stays the NULLIF tenant-pin (no PLATFORM branch, so write-nothing/impersonate-only is structural); `audit.events` keeps its USING-only outlier. Migration `0011` DROP+CREATEs them and the 13 `schemas/postgres/*.sql` DDL files carry the same end-state (fresh==migrated, `test_migration_0011` catalog equality). `dis-rls` adds `rls_platform_session` (sets `app.user_type='PLATFORM'` + empty/see-all or impersonation `app.tenant_id`) sharing the first-use posture guard; `rls_session` now sets `app.user_type='TENANT'`. The auth seam reads a REQUIRED `user_type` claim (reject-on-ambiguous); `require_read_scope` gates PLATFORM see-all on `user_type=PLATFORM` AND `dis:ops`; `POST`/`PATCH /mapping-templates` impersonation-writes take a PLATFORM-only body-supplied acted-for tenant (`resolve_acted_for`; a TENANT naming one is 403). Registers D91/D92/D93. `services/dis-ui` untouched. Exit: PLATFORM+`dis:ops` reads across all tenants and writes only an impersonated tenant; TENANT unchanged and isolated; catastrophe property test-proven.
- `TODO` Slice 17c: seeder writes identity_mirror per-tenant (both GUCs, RLS-off-safe). Test-infra only; no DDL/migration; prep for a later optional RLS-on slice.
- `DONE` Slice 8: CSV upload, Phase 1 (dis-ui-server synchronous endpoint). SUPERSEDES the inherited signed-URL design (D72): with the 10 MB ceiling there is no large-file case, so `POST /api/v1/csv-uploads` streams the file THROUGH the server in one multipart request (`file` + `template_id` + `store_code`) — no upload-session object, no signed PUT URL, no completion detection (closes D54's open fork; D36's placement and D54's worker-trust model stand). The handler: tenant + user from the token only; 10 MB enforced MID-STREAM (`upload_stream.py`, the reusable file-body pattern, proven at the ASGI boundary); tier-0 structural gate (D51); template validated ACTIVE via `rls_session` (the ACTIVE row supplies `source_id`); `store_code` resolved via the mirror in-query chokepoint then gated ACTIVE-only (404 resolve before the 409 gate — no existence oracle); object written at the canonical D53 path; audit; `csv.received` published carrying the resolved identity + codes + `trace_id` + required `template_id` (D71) + a DETERMINISTIC `upload_session_id` (`us_` + 12-hex of SHA-256 over tenant|store|template|content-hash) so client retries collapse in the worker's D58 dedup. GCS-write-then-publish; a post-write publish failure leaves an accepted orphan object (no compensating delete; the retry converges). The template_id carry: required on `csv.received` AND `ingress.ready`; the worker passes it through and persists it to bronze (`template_id` column, migration 0006, replay lineage — D73); the streaming consumer PARSES it (envelope drift guard) but its mapping lookup stays template-unaware until Slice 8a (D71 hard gate: no second-ACTIVE-template path before 8a — regression-pinned in the consumer's tests).
- `DONE` Slice 8a: consumer template-keyed mapping lookup (the fix owed by D71; consumer-only). `load_active_mapping` now keys on `(tenant_id, source_id, template_id, status='ACTIVE')` — the `uq_csm_active_per_source` index makes the lookup single-row, `.first()` exact — so each CSV is processed with the exact template's `mapping_rules` it was uploaded against; a `template_id` naming no ACTIVE row raises a clean template-grained `MappingConfigError` (never a silent wrong-mapping); `template_id` recorded on the `MAPPING_LOOKED_UP` audit `event_data`. The Slice 8 regression pin retired and replaced by its inverse; the core property mutation-proven (removing the predicate fails the two-ACTIVE-templates test, the unknown-template test, and the source pin — three independent kills). `template_id`-absent resolved as structurally unreachable (D74: envelope contract-reject, terminal ack before the pipeline; no fallback code). D22 (`mapping_version_id` stamp) and D33/D65 (dedup) proven unchanged by UNMODIFIED tests now running through the keyed lookup. D71 `RESOLVED`, its hard gate LIFTED: the promote/reject/shadow slice is unblocked.


### Audit pipeline

The audit trail (`audit.events` in Cloud SQL, Phase 1; BigQuery archive at Slice 21 per D34)
must record a complete, queryable story of what happened to every CSV, keyed by `trace_id`,
including failures. The table and the `dis-audit` writer exist (Slices 1, 6) and the consumer
success ladder emits well, but coverage has gaps (silent stages, under-populated failure rows)
and the partition design has a silent write-cliff. These slices make the audit pipeline correct
for beta. The failure-audit shape defined here is the seam the quarantine work consumes.

- `DONE` Slice 30a: De-partition `audit.events` (remove the silent write-cliff). `audit.events`
  was range-partitioned by `event_date` with a fixed bootstrap-only set of daily partitions and no
  DEFAULT partition and no automation (D45); past the last partition, every audit write hit "no
  partition found", which fire-and-forget swallows, so audit silently stopped recording. Now a
  plain (non-partitioned) table: a write for any `event_date` always lands (test-proven both
  sides of the old window); partitioning with automation is re-introduced at Slice 21 (BQ
  archive + eviction), the slice that actually needs it. Built as migration 0007
  (drop-and-recreate from `events.sql`, data disposable) + the one-tuple removal from 0001's
  `PARTITIONED` list, so fresh-bootstrap == migrate-existing (scratch-DB catalog-equality
  proven). PK `(id, event_date)` → `(id)`; `event_date` stays a column; the event_date-matches
  CHECK KEPT (column semantics + Slice 21's re-partition invariant); RLS and the non-partition
  constraints preserved verbatim; the `dis-audit` writer unchanged. Scope was `audit.events`
  ONLY: the canonical event tables share the scheme but are the D29/D34 eviction substrate and
  fail LOUD (batch nack) on a missing partition — they keep their partitioning, test-pinned.
  Registered as D77; D45 → `RESOLVED-for-beta`. Unblocks Slice 30b (coverage on a sink that
  reliably accepts writes).

- `DONE` Slice 30b: Audit coverage and the failure-audit shape — Tier 1, the code-only spine
  (operator split: Tier 2 / the DDL enrichment is Slice 30c). The load-bearing piece: **the
  failure-audit shape, registered as D78** — the contract the quarantine work consumes (trace +
  tenant always; `data_ingress_event_id` post-bronze; `mapping_version_id` post-lookup; a stable
  `failure_code`; ids never buried in `failure_message`). The consumer catch-all threads its
  known ids via `_FlowContext` (the storm's NULL-id failure shape is no longer producible —
  mutation-test-enforced). **The `FailureCode` vocabulary, registered as D79** — a closed
  27-member StrEnum in dis-audit replacing exception-class-name codes, zero DDL (`failure_code`
  is a CHECK-less varchar; `INFRA_FAILURE` + `event_data.exception_class` is the no-loss
  fallback; the D63 miss got a dedicated `HotPositionMissingError`). Also landed: the dis-ui 4xx
  family audited (emit-then-re-raise; HTTP status + §2.3 envelope unchanged, proven incl. under
  a THROWING audit backend); `RETRIED` on consumer redelivery (best-effort audit readback,
  degrades to SUCCESS and never wedges — pipeline-level-proven; `delivery_attempt` is the
  post-DLQ upgrade); the worker gaps (path-mismatch `event_data`; PII-block coded + counted —
  `bronze_id` correctly stays NULL: the gate runs BEFORE the bronze write per hard rule 2, the
  scoped line above was wrong); `duration_ms` per stage via lap timers on INGRESS_EVENT rows.
  ZERO schema change (gate-proven: nothing under schemas/ or alembic/; writer/model untouched).
  *Slice 30c carries Tier 2: the D42 revision (DUPLICATE_* outcome CHECK + `prior_trace_id`
  column, duplicates emit JSONB until then), the drift-guard type/nullability hardening.*

- `DONE` Slice 30c: Audit Tier 2 — the outcome vocabulary, `prior_trace_id`, and the drift-guard
  hardening. **The D42 REVISION, registered as D80**: `DUPLICATE_NOOP`/`DUPLICATE_OVERWRITTEN`
  promoted to first-class outcome values (the CHECK extends 4 → 6) and `prior_trace_id` to a
  column, consciously superseding Slice 10's deliberate event_data-JSONB resolution — reason:
  console queryability ("duplicate rate per tenant" / "what redelivered from what" are column
  queries now). DUPLICATE_* refine SUCCESS (the insert landed, D33); `row_hash`/`dedup_key` stay
  in event_data; the worker resume-publish is `RETRIED`. Migration 0008: ADDITIVE on the plain
  30a table (real rows, no drop-recreate), a TRUE NO-OP on a manifest-fresh DB (gate-firing
  proven by a stamp-rerun test), fresh == migrated (scratch-DB catalog equality), and a
  REFUSE-LOUDLY downgrade (named message + count, rows untouched — assertion-pinned after the
  adversarial pass caught the original test passing on alembic's banner). **The drift-guard
  hardening, registered as D81 (its own entry — the D45 silent-loss class)**: the dis-audit
  guard now checks type + nullability + length against the live schema via a pure fail-loud
  diff, narrowing-proven synthetically; drift fails loud at the guard, not silently at INSERT
  under fire-and-forget. The column promotion is mutation-test-enforced (outcome revert and
  prior_trace_id revert each fail the flipped duplicate test). **The audit-coverage arc (30a
  de-partition D77, 30b coverage D78/D79, 30c promotion D80/D81) is complete**; the de-partition
  pattern 30a opened is carried to the six canonical/staging parents by Slice 30d (D85, below).
  What remains of the audit surface is owned elsewhere (the QUARANTINED emitter + DLQ by the
  quarantine work; BQ archive + re-partition by Slice 21).

- `DONE` Slice 30d: De-partition the 6 canonical/staging event + signal_history parents — the
  de-partition follow-on to 30a. **Registered as D85**, revising D77's scope clause (which kept
  them partitioned) on the 0007 disposable-rows pattern: migration 0009 drops + re-applies each
  DDL file plain, PK `(id,date)` → `(id)`, with `event_date`/`as_of_date` + their derivation
  CHECKs + the signal_history natural keys kept as Slice 21's re-partition invariants; 0001's
  manifest emptied (fresh == migrated), reversible, the 30a scope-boundary test inverted in
  `test_migration_0009.py`. Partitioning returns WITH automation at Slice 21 for all 7 tables.

Deferred / owned elsewhere (named so they are not lost):
- The `QUARANTINED` audit emitter (the Stage enum has the value, nothing emits it) is owned by
  the quarantine work, not these slices.
- Pub/Sub dead-letter + max-delivery-attempts (the backstop that breaks a deterministic-failure
  redeliver loop, the storm Slice 30a's precursor arrested) is owned by the quarantine / DLQ
  work; cheap and worth early.
- BQ archive + partition eviction + the real audit retention policy is Slice 21 (Phase 3); it
  re-introduces partitioning with automation.
- The unparseable-envelope silent case (D43 structural: tenant unknowable) is documented as
  silent-by-design unless a tenant-less audit row is later wanted; decision, not code.

### Daily compute

- `TODO` Slice 18: Daily compute. Produces `store_sku_signal_history` rows per (store, SKU, as_of_date); updates derived columns on `store_sku_current_position`; ROOS has fresh signals every day.

**Phase 1 exit criterion.** All non-DEFERRED slices DONE. A tenant can upload a CSV via the UI, have it land in canonical, see failures in the quarantine console, and audit events for every pipeline step are queryable from Cloud SQL via dis-ui-server. BigQuery archive is deferred to Phase 3.

### GCP Staging Deployment

\- `TODO` Slice 40a: Cloud-wiring (app side). The three Pub/Sub clients (dis-ui-server publisher, csv-ingest-worker + streaming-consumer subscribers) gain a real-GCP mode: emulator when `PUBSUB_EMULATOR_HOST` is set (local, unchanged), ambient service-account credentials against real Pub/Sub when it is not, mirroring the dis-storage emulator-or-ambient pattern (resolves audit finding A2; the workers and the upload publish currently refuse real GCP by design). The two pull-loop workers gain a readiness `/healthz` HTTP server (reads `$PORT`, serves `/healthz`, returns unhealthy when the pull loop's heartbeat is stale) so they pass Cloud Run Service health checks while the loop runs in the background (resolves A5 for the Service shape). Built switch-ready: the healthz server is behind a runtime env-var toggle around an unchanged core loop, so the later move to Cloud Run Worker Pools (requested, invitation-only) is a terraform/config change with no app code change. mirror-sync is a Cloud Run Job + Scheduler (infra), unchanged here. *Depends on / hands off to: the infra-side fixes (env-var names, contract topic/subscription names, Dockerfile/Cloud Build paths, secret values, the ui-server publisher role, the Pub/Sub DLQ policy, the worker Cloud Run shapes) are Amit's terraform, tracked separately; the health-check contract (port `$PORT`, path `/healthz`, readiness) is documented and shared with Amit so his Service config matches.*

---

### DIS UI

- `TODO` Slice 19: DIS UI foundation. `ui/` initialized; auth scaffolding against Customer Master tokens; a hello-world page calls a dis-ui-server endpoint and renders the response. Stack and tool choices made during this slice.
- `TODO` Slice 20: DIS UI core. Operator/tenant can upload a CSV, review the onboarding result, edit the mapping config, inspect the quarantine console, look up audit events, and resubmit failed chunks.

## Phase 2: Integration

Phase 1 slices test against fakes individually. Phase 2 verifies the full system wires together.

- `TODO` Identity flow end-to-end: a tenant added to Customer Master DB appears in `identity_mirror` after the next Mirror Sync DB-pull run; downstream services pick up the new tenant on their next request. Pub/Sub-driven incremental sync is a later test, triggered when Customer Master emits `identity.changed`.
- `TODO` Receiver → streaming consumer end-to-end: CSV uploaded by tenant A cannot be read by tenant B; RLS holds across the pipeline.
- `TODO` Quarantine flow end-to-end: a malformed CSV row reaches the tenant quarantine console; the resubmit button rebuilds the chunk and processes successfully.
- `TODO` Onboarding flow end-to-end: a new tenant uploads a sample, reviews the suggested mapping, promotes to active, and uploads a real CSV that lands in canonical without operator intervention beyond mapping approval.
- `TODO` Audit and quarantine end-to-end: every Phase 1 pipeline step emits audit events to Cloud SQL `audit.events`; quarantined chunks are queryable by tenant via dis-ui-server; trace_id chains a single ingress event from receiver through canonical.
- `TODO` Daily compute end-to-end: synthetic day-window events produce the expected `signal_history` row and `current_position` updates.
- `TODO` Replay end-to-end: an ops engineer replays a bronze chunk and sees the chain (parent_trace_id, new trace_id, audit events) in the dis-ui-server.
- `TODO` Full e2e: every flow above runs simultaneously against a multi-tenant test environment without cross-contamination.

**Phase 2 exit criterion.** Every e2e test in `tests/e2e/` passes. The system handles ingress, processes, surfaces failures, and replays end-to-end.

---

## Phase 3: Production hardening and analytics offload

### Resilience

- `TODO` `pipeline.dlq` auto-drainer. A service or sidecar consumes the `pipeline.dlq` topic and replays messages every 60s when the Cloud SQL health probe reports recovery. Architecture circuit-breaker pattern (decisions.md D27). v1.0 launch operates with manual recovery; auto-drainer activates here.

### Analytics offload to BigQuery

- `TODO` Slice 21: Nightly batch and BigQuery archive. Daily Cloud SQL → BigQuery export populates `canonical_history.*`; `audit_events` from Cloud SQL is archived to BigQuery; Cloud SQL partitions older than the retention window are dropped; dbt freshness/completeness tests pass. *Trigger: ROOS or another consumer needs long-term canonical history, OR Cloud SQL retention pressure justifies offload.*

### Production hardening

- `TODO` Observability dashboards show latency p50/p95/p99, DLQ depth, quarantine rate per tenant, audit emission rate.
- `TODO` Performance tuning: Cloud SQL indexes verified against real query patterns; BigQuery query patterns optimised; no silent N+1 or table scans.
- `TODO` Cost optimisation: committed-use discounts applied where stable; logging retention tuned; actual monthly bill matches projection within 20%.
- `TODO` Cloud SQL HA enabled. *Trigger: first paying tenant SLA mandating 99.99%.*
- `TODO` Cloud SQL read replica live. *Trigger: read CPU sustained above 60%, OR p95 read latency above 200ms.*
- `TODO` Memorystore Redis identity cache. *Trigger: identity resolves above 10k/sec, OR in-process cache hit rate drops below 80%.*

**Phase 3 exit criterion.** Production is observable, performant within SLO, cost is understood. Each migration trigger has either fired and been actioned, or remains explicitly accepted.

---

## Migration triggers (consolidated reference)

Deferred items live in the phases above; this section gathers the triggers for quick scanning. First trigger met wins.

- Higher-throughput streaming runtime (Beam on Dataflow) — *sustained 500+ rows/sec for 7 days, OR consumer scaling above 20 concurrent instances, OR p95 above 10s. See decisions.md D4.*
- Cloud SQL HA — *first paying tenant SLA, OR single-zone outage incident.*
- Cloud SQL read replica — *read CPU sustained above 60%, OR p95 read latency above 200ms.*
- Redis identity cache — *identity resolves above 10k/sec, OR in-process cache hit rate drops below 80%.*
- Historical-learning onboarding — *20+ approved mappings in `config.source_mappings`.*
- LLM-assisted onboarding — *tenant onboarding time-to-active exceeds 1 week consistently.*
- Machine auth migration to Customer Master — *Customer Master scope expands to cover machine credentials.*
- Trace-level dedup at streaming-consumer entry — *`DUPLICATE_NOOP` audit volume sustained above 10% of total. See architecture.md §9.2.*
- BigQuery audit dataset isolation — *audit query load impacts BQ slot budget for canonical_history dbt runs.*

---

## Build target portability

The same code runs against three environments via env-var-driven routing. Operator picks the target; code does not change.

| Target | Used for | Dependencies route to |
|---|---|---|
| `local` | Developer machines, `make run-local`, local tests | docker-compose emulators and fakes |
| `dev` | Shared dev GCP project | Real GCP services in `ithina-dis-dev` |
| `staging` | Pre-production verification | Real GCP services in `ithina-dis-staging` |
| `prod` | Production | Real GCP services in `ithina-dis-prod` |

Switch via `DIS_TARGET=local|dev|staging|prod`. Service code uses standard client libraries; env vars (e.g., `PUBSUB_EMULATOR_HOST`, `STORAGE_EMULATOR_HOST`, `POSTGRES_URL`) route to the right backend. Detail in `local-setup.md` §B.

---

## Slice workflow

Slices are how Claude Code builds DIS. The shape:

1. **Draft the slice in this Claude AI chat** with the operator. Capture: goal, task description, scope boundary, acceptance criteria. Save to `docs/slices/slice-NN-<short-name>.md`. Slice doc stays at goal/task level; implementation specifics emerge in plan mode.
2. **Hand slice doc + execution prompt to Claude Code.** Claude Code reads, enters plan mode (Shift+Tab twice), returns a plan.
3. **Review the plan in this chat.** Operator decides: execute, or revise.
4. **Revise loop.** If plan needs changes: feed corrections back to Claude Code; re-plan; re-review. Repeat until the plan looks right.
5. **Execute.** Operator tells Claude Code to proceed; Claude Code writes code; operator reviews diffs.
6. **Slice exit.** Acceptance criteria met → merge → mark the slice DONE in this doc. Anything learned that affects future slices goes into root or per-service `CLAUDE.md`.

### When to intervene
- Plan looks wrong → correct in the chat, revise the slice doc if needed, re-plan.
- Tests fail in a way that suggests a slice constraint is violated → push back.
- Claude Code proposes scope outside the slice → hold the line. New scope = new slice.
- A CLAUDE.md invariant gets broken → fix the invariant statement first, then re-execute.

### CLAUDE.md hygiene
- Root `CLAUDE.md` under 200 lines.
- Per-service `CLAUDE.md` under 100 lines.
- Per-lib `CLAUDE.md` under 50 lines.
- New invariants discovered during a slice go into the relevant CLAUDE.md before the next slice starts.

### Common pitfalls
- **Over-specifying the slice.** Slice docs name goal and scope. They don't name HTTP status codes, library versions, response field shapes. Those come from plan mode.
- **Letting Claude Code propose architecture.** Plan mode is implementation. If the plan changes architecture, stop and raise in this chat.
- **Skipping plan mode.** Fast short term, expensive over weeks. Plan mode is the leverage point.
- **Not updating CLAUDE.md.** Every slice teaches the system something. Capture it.

---

## Document lifecycle

This doc mutates. When:
- A slice or item completes: change status to DONE.
- A new slice is identified: add it under the right phase.
- A migration trigger fires: change the relevant DEFERRED item to TODO and start it.
- A whole section becomes obsolete: delete it.

When this doc grows past ~300 lines, consider splitting.
