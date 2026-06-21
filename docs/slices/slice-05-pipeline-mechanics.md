# Slice 5: Pipeline mechanics (dis-mapping) and validation (dis-validation)

## Depends on

- Slice 1 for the applied canonical schema, which fixes the shape any mapped row
  must satisfy and the shape the canonical-shape validation suite checks against.
  The source of truth is the live schema in the DIS database (`ithina_dis_db`,
  port 5433), not the DDL files; field shapes are confirmed by read-only
  introspection in plan mode.
- Slice 3 for `dis-core` (`errors` with the single `DisError` root, `ids` for
  UUIDv7, `timestamps`, structured `logging`) and `dis-canonical` (the Pydantic
  models that are the single description of canonical shape). The canonical-shape
  suite derives its field set, types, and nullability from `dis-canonical`; both
  are reused without disturbance or duplication.
- Decisions this slice must honour: D4 (the transformation logic is pure
  functions over `(mapping, raw_row) -> canonical_contribution`, so a later Beam
  migration is a runner swap, not a rewrite); D8 (a canonical row is the union of
  source-owned columns merged at write time, so one source's mapping produces only
  the columns that source owns, not a complete row); D4a / D21 (Pandera is the
  validation engine);
  D18 (validation splits into pre-mapping source-shape and post-mapping
  canonical-shape); D20 (normalization is a distinct sub-stage between rename and
  cast, declarative, with `rename -> normalize -> cast -> derive` ordering
  mandatory). D15 places mapping config in `config.source_mappings`, but loading
  it is the consumer's job (Slice 10), not this slice's.
- CLAUDE.md hard rules: errors from `dis-core/errors.py` (no raw
  `RuntimeError`/`ValueError`); UUIDv7 via the `dis-core` `ids` helper; structured
  logging binds `tenant_id`, `trace_id`, `service`, `stage`; never log PII or raw
  payloads. Hard rule 5 (mapping version pinning) is stamped by the streaming
  consumer, not by the mapping engine; this slice must not stamp it.
- No forward dependency blocks this slice. Downstream consumers are the streaming
  consumer (Slice 10), which wires these libs into the pipeline and owns config
  loading, failure routing, canonical writes, version stamping, and audit; and the
  onboarding sub-module (Slice 14), which generates draft mappings and suites.
  They size the surface area.

## Goal

After this slice, the two pure libraries that are the logic of the pipeline exist
and are importable. `libs/dis-mapping` takes a tenant's parsed, in-memory input
and applies that tenant's mapping in four ordered sub-stages (rename, normalize,
cast, derive), producing a canonical contribution: the columns this source owns,
correctly shaped, but not a complete canonical row. It does not populate the
columns the consumer injects after it runs (identity `tenant_id`/`store_id`,
`trace_id`, `mapping_version_id`), and the merge of multiple sources into one
canonical row is the consumer's column-scoped write (D8). It fails loud and typed
when a value cannot be normalized, carrying the column, value, and expected
format. `libs/dis-validation` runs two Pandera suites: a source-shape suite that
judges a raw chunk in the tenant's own vocabulary before mapping, and a
canonical-shape suite that judges a mapped contribution against the invariants of
the columns it owns after mapping, each returning pass or a typed, tenant-readable
failure.

Both libs are pure: no Postgres, no GCS, no Pub/Sub, no network, no file I/O. They
operate on data and a mapping or suite handed in by the caller and return a result;
the D4 runner-swap guarantee rests on this, so the same code wraps in today's
container consumer and tomorrow's Beam DoFns. No service, consumer, routing,
quarantine, canonical-write, version-stamping, or audit logic is built here, and
neither lib loads config from the database. Unlike Slice 4, this slice writes
nothing to Postgres, so there is no target-safety pass for writes; the only live
contact is read-only schema introspection in plan mode against `ithina_dis_db` on
5433, never Customer Master on 5432.

## Task

Build two libs in the directories the repo already reserves for them; confirm
exact placement in plan mode rather than inventing dirs.

1. `libs/dis-mapping`: the mapping engine, as pure functions over a mapping and
   parsed, in-memory input (a frame or rows, not file bytes; parsing the upload
   and any file access is the caller's job), producing a canonical contribution
   of the source-owned columns. Four ordered sub-stages:
   - **rename**: source field names to canonical field names.
   - **normalize**: canonicalize representation per the mapping's declared format
     for each column (date formats, decimal separators, timezones, units, enums,
     booleans, null encodings, casing, whitespace). The format is asserted by the
     mapping, never inferred. Placement before cast is mandatory (D20).
   - **cast**: string to target type, now safe because normalize produced a
     canonical representation.
   - **derive**: canonical fields computed from others, expressed through the same
     bounded declarative vocabulary as normalize. Arbitrary derive logic is the
     same deferred escape hatch (see scope boundary), not an in-slice surface.
   The engine ships the bounded declarative vocabulary only. The
   named-custom-transform escape hatch is deferred (see scope boundary). On a value
   that cannot be normalized, the engine reports a typed failure at per-cell grain
   (column, value, expected format); the row carrying that cell produces no
   canonical contribution, and its per-cell failures are returned alongside the
   rows that succeeded. The engine applies no pass-threshold and does not route the
   failure anywhere (B2 deferred to Slice 10). It does not stamp
   `mapping_version_id` (the consumer's job, hard rule 5), does not populate the
   consumer-injected identity or `trace_id` columns, does not produce columns
   absent from the live canonical schema (relates to D38, Slice 10), and receives
   already-resolved internal identity, performing no external-to-internal id
   translation (D37, Slice 7).

2. `libs/dis-validation`: two Pandera suite types and the machinery to run them,
   as a pure lib that takes data and a resolved suite (or its definition) and
   returns pass or a typed failure. It performs no DB access.
   - **source-shape (pre-mapping)**: judges a raw chunk in the tenant's vocabulary
     (required source columns present, type sniff for plausibility only, null and
     row-count plausibility, encoding sanity). The set of expected source columns
     overlaps the mapping's rename map; plan mode names whether the suite derives
     them from the mapping or carries its own list (see open question 4). Produces
     a tenant-readable reason on failure (for example, "expected column
     `item_code`, got `itemcd`").
   - **canonical-shape (post-mapping)**: judges a mapped contribution against the
     invariants of the columns it owns, scoped to one named target canonical model.
     Field set, types, and nullability are derived from that `dis-canonical` model,
     restricted to the source-owned, mapping-produced columns and excluding the
     consumer-injected columns (identity, `trace_id`, `mapping_version_id`); the
     business invariants (for example range bounds, cross-field consistency,
     identifier patterns) are authored in the suite. Existence checks (does this
     `tenant_id`/`store_id` exist in `identity_mirror`) are NOT in this lib: they
     are a DB read the pure lib cannot do, and belong to the consumer at write time
     (Slice 10). A drift guard asserts the suite's column set against the target
     model's mapping-produced columns, both directions, so the suite and the
     canonical model cannot drift apart. `store_sku_signal_history` is out: it is
     daily-compute output, not mapping-produced (D22, D32), so it has no
     mapping-time suite.
   - **suite materialization**: turn a handed-in suite definition into a runnable
     Pandera schema. Resolving which version is active and fetching it from
     `config.source_mappings` is the consumer's side-input (Slice 10), not this
     lib's.
   - **failure formatting**: turn Pandera's raw failure output into the typed,
     tenant-readable reasons the quarantine console needs, keeping the three
     failure types (source-shape, normalization, canonical-shape) distinct (D18,
     D20). Normalization failures originate in `dis-mapping`; this lib does not
     re-detect them.

## Acceptance criteria

1. `libs/dis-mapping` and `libs/dis-validation` are importable. `dis-mapping`
   depends on `dis-core` (and its stack libs; whether it also depends on
   `dis-canonical` is resolved in plan mode by how it types its output);
   `dis-validation` depends on `dis-core` and `dis-canonical`. The import graph is
   acyclic, neither lib depends on the other, and neither depends on `dis-rls`,
   `dis-pii`, or `dis-storage`; an import-linter contract enforces this. Neither
   lib performs any Postgres, GCS, Pub/Sub, network, or file I/O at runtime; since
   the import graph alone does not prove the absence of I/O, this is held by a
   contract test (no socket or file open at runtime) plus review, not assumed.
   `make check` shows no tier regression and the new tests pass.
2. `dis-mapping` applies the four sub-stages in the `rename -> normalize -> cast
   -> derive` order. A test proves the normalize-before-cast ordering is
   load-bearing: an input that a cast-first path would fail (for example a decimal
   in a comma-separator locale) passes when normalize runs first. Given a mapping
   and parsed input, the engine produces a canonical contribution carrying the
   source-owned mapped columns only; it does not populate the consumer-injected
   columns (identity, `trace_id`, `mapping_version_id`), and a test confirms those
   are absent from the engine's output.
3. A value that cannot be normalized produces a loud, typed failure carrying the
   column, the value, and the expected format, at per-cell grain. The row carrying
   that cell produces no canonical contribution; its per-cell failures return
   alongside the rows that succeeded. A test confirms the failure is surfaced at
   per-cell grain and that a partially-failed row yields no output. That the engine
   applies no pass-threshold and performs no routing (B2 is the consumer's at
   Slice 10) is a review-only property: a test cannot prove the absence of a
   feature, so it is confirmed by review, not asserted as green.
4. The source-shape suite judges a raw chunk in the tenant's vocabulary and
   returns pass or a typed, tenant-readable failure; a test exercises both a
   passing chunk and a failing one with an intelligible reason.
5. The canonical-shape suite judges a mapped contribution and returns pass or a
   typed failure. Its field set, types, and nullability are derived from one named
   `dis-canonical` model, restricted to the source-owned, mapping-produced columns
   and excluding the consumer-injected columns; its business invariants are
   authored as checks (not DB calls). It contains no `identity_mirror` existence
   check (that is the consumer's at write time). A test exercises a passing
   contribution and one failing a business invariant.
6. A drift guard test asserts the canonical-shape suite's column set equals the
   target model's mapping-produced column set (excluding consumer-injected
   columns), both directions, so the suite cannot silently diverge from the
   canonical model. `store_sku_signal_history` is excluded, as it is not
   mapping-produced. This proof errors rather than skips if its inputs are absent.
7. Both libs raise `dis-core` errors (no raw `RuntimeError`/`ValueError`), bind
   `tenant_id`, `trace_id`, `service`, `stage` in logs where applicable, log no PII
   or raw payloads, and mint any UUIDs via the `dis-core` `ids` helper.
   `dis-mapping` does not stamp `mapping_version_id` and does not emit columns
   absent from the live canonical schema.
8. Each lib's `CLAUDE.md` records its new invariants before slice exit (per-lib
   `CLAUDE.md` under 50 lines).

## Scope boundary

In scope:
- `dis-mapping`: the pure four-sub-stage engine and the bounded declarative
  normalizer vocabulary.
- `dis-validation`: the two suite types, suite materialization from a handed-in
  definition, the canonical-shape derivation from `dis-canonical` plus authored
  invariants, the drift guard, and the failure formatter.
- The tests that prove ordering, per-cell failure reporting, both validation
  gates, and the drift guard.

Out of scope (do not let the slice sprawl):
- Loading mapping config or suites from `config.source_mappings`. The consumer
  owns the database read as a refreshing side-input. *Slice 10.*
- Routing failures to quarantine, writing canonical rows, the atomic dual-write,
  `mapping_version_id` stamping, and audit emission. *Slice 10.*
- The named-custom-transform escape hatch and its registry. The stance is
  declarative-only: a gap the bounded vocabulary cannot express is an onboarding
  problem (fix the mapping or extend the shared vocabulary) or schema drift (detect
  and fail), not a per-source code path. *Deferred. Trigger: a concrete source that
  defeats both the declarative vocabulary and a vocabulary extension.*
- The normalization-failure pass-threshold and the chunk-versus-row routing
  decision (B2, OPEN). The engine reports per-cell pass/fail only; the threshold
  and routing belong where failures are routed to quarantine. *Deferred to
  Slice 10.*
- Proactive schema-drift monitoring (a standalone watcher or pre-processing
  schema comparison). Drift is caught reactively: structural drift by the
  source-shape suite, format drift by normalization, both into quarantine. *New
  scope; not in any current slice.*
- PII handling. Tokenization happens at the receiver before bronze (D24), so the
  mapping engine operates on already-tokenized data and takes no `dis-pii`
  dependency. Confirm this in plan mode.
- Resolving D37 (external `t_*`/`s_*` vs internal UUID keys; OPEN, Slice 7) and
  D38 (event-table dedup columns absent from the schema; OPEN, Slice 10). The
  engine receives resolved internal identity and emits only columns the live
  canonical schema carries, so neither is this slice's to settle.
- Reopening the Polars or Pandera stack choices. Both stand; this slice captures
  the Polars rationale gap (open question 6), it does not relitigate the pick.
- Authoring or changing any DDL. If a needed column is missing or a shape is
  wrong, surface it in plan mode and register it; do not edit DDL here.
- Building lib surface later slices do not yet need (for example per-tenant
  batching, a custom-transform registry, or speculative normalizers). Build to
  current and upcoming need; later slices extend.

## Constraints

- Both libs are pure: no Postgres, GCS, Pub/Sub, network, or file I/O at runtime.
  The D4 runner-swap guarantee depends on this, so the engine and suites stay
  portable across the container consumer and a future Beam pipeline. Plan-mode
  introspection of the live schema is read-only, against `ithina_dis_db` on 5433,
  never 5432.
- The engine's output is a partial canonical contribution: the source-owned,
  mapping-produced columns only. Identity (`tenant_id`, `store_id`), `trace_id`,
  and `mapping_version_id` are consumer-injected after the engine runs, and the
  merge of multiple sources into one canonical row is the consumer's column-scoped
  write (D8). The engine populates none of these.
- The engine takes parsed, in-memory data. Parsing the upload into rows and any
  file or object access is the caller's; the engine never reads bytes or files.
- A per-cell normalization failure means that row yields no canonical
  contribution; the per-cell failures return alongside the rows that succeeded.
  This row-level outcome is distinct from B2 (the deferred threshold and routing).
- Existence checks against `identity_mirror` are not in either lib; they require a
  DB read the pure libs cannot do and are the consumer's at write time. The
  canonical-shape suite checks shape, type, and authored invariants only.
- `rename -> normalize -> cast -> derive` ordering is mandatory; normalize before
  cast is load-bearing, not stylistic (D20).
- Derive is bounded to the same declarative vocabulary as normalize; arbitrary
  derive computation is the deferred escape hatch, not an in-slice surface.
- Format is asserted by the mapping, never inferred. A declared format that a value
  violates fails loud; the engine does not guess a format.
- Failures are typed and carry a tenant-readable reason plus the load-bearing
  context (column, value, expected format for normalization). The three failure
  types (source-shape, normalization, canonical-shape) stay distinct (D18, D20).
- The engine reports normalization failure at per-cell grain. It applies no
  pass-threshold and performs no routing (B2 deferred to Slice 10).
- `dis-mapping` does not stamp `mapping_version_id` (hard rule 5; the consumer
  stamps it) and emits no column absent from the live canonical schema (D38).
- `dis-mapping` receives already-resolved internal identity; it performs no
  external-to-internal id translation (D37 is not needed here).
- The canonical-shape suite derives its field set, types, and nullability from one
  named `dis-canonical` model, restricted to the source-owned, mapping-produced
  columns (excluding consumer-injected columns); the drift guard asserts that
  column set both directions so the suite and the canonical model cannot drift.
- Properties that assert the absence of behaviour (no routing, no threshold, no
  runtime I/O) cannot be proven by a passing test. They are held by an
  import-linter contract and a contract test where possible, and by review
  otherwise; the doc marks which is which so green is not over-read.
- Errors inherit from the single `dis-core` `DisError` root; no raw
  `RuntimeError`/`ValueError`. UUIDv7 only via the `dis-core` `ids` helper.
  Structured logging binds the four keys; never log PII or raw payloads.
- The escape hatch is not built; it is named as a later seam in docs. No registry
  code lands.
- Load-bearing proofs (the ordering test, the drift guard) error rather than skip
  when an input is absent; a silent skip reports green without having run.
- New per-lib invariants are captured in each lib's `CLAUDE.md` before slice exit.
- Two libs in one slice: keep the acceptance criteria separable per lib, and
  confirm in plan mode each lib's exact dependency set.

## Open questions (for plan mode to resolve)

1. Engine surface: per-row versus per-chunk. The DataFrame layer (Polars) pushes
   `dis-mapping` toward operating on a chunk, a column at a time, rather than one
   row in isolation. Decide what the engine operates on and returns (a Polars
   frame, plain dicts, or `dis-canonical` model instances), and how per-cell
   normalization-failure reporting is expressed on that surface. This interacts
   with criterion 3 and with whether `dis-mapping` depends on `dis-canonical`.

2. The `mapping_rules` shape and the `transforms`-versus-`normalize` field name.
   Introspect the live `config.source_mappings.mapping_rules` shape. D40's note
   recorded `{version, rename, normalize, cast, derive}` (field named `normalize`),
   while `architecture.md` and D20 describe a `transforms` field driving
   normalization. Confirm which the live schema uses before the engine reads it;
   register the divergence with its own D-number only if introspection confirms a
   genuine mismatch, not from the snapshot alone. Confirm no live RLS posture
   matters here, since the lib reads nothing live at runtime.

3. The bounded normalizer vocabulary. Enumerate the declarative normalizers
   actually needed now (for example parse-date, parse-decimal, parse-boolean,
   enum-lookup, currency, whitespace, null-encoding, casing) against current and
   upcoming consumer need; do not build speculative normalizers. Confirm each is
   expressible as config the mapping selects per column.

4. The suite "definition" the loader consumes, and where its columns come from.
   Define the input shape for each suite: the source-shape definition (a raw spec,
   no canonical model behind it) and the canonical-shape definition (the
   target-model's mapping-produced columns plus authored invariants). Name where
   the source-shape suite's expected columns come from: derived from the mapping's
   rename map (a coupling to acknowledge) or carried as a standalone list (a
   duplication that can drift). Confirm Pandera supports the needed check styles
   (cross-field invariants, identifier regex) and can produce failure detail at the
   grain the quarantine console needs. If Pandera cannot cleanly support a needed
   style, surface it rather than working around it.

5. Dependency direction and placement. Confirm `dis-mapping`'s dependency
   (`dis-core` only, or also `dis-canonical` if it types output as canonical
   models), that `dis-validation` depends on `dis-core` and `dis-canonical`, that
   neither lib depends on the other or on `dis-rls`/`dis-pii`/`dis-storage`, that
   the graph is acyclic, that neither does DB access, and the exact reserved
   directory for each. Do not invent dirs.

6. The Polars rationale gap. Polars appears once in the snapshots (a CLAUDE.md
   stack line) with no recorded rationale and no decision entry; the register's
   scope rule excludes library picks, so the gap is a missing rationale, not a
   missing D-number. Confirm against the live repo that no rationale exists
   elsewhere, then capture a short why (Polars over pandas, plus the per-chunk
   consequence) where stack rationale belongs. Decide register-versus-CLAUDE.md-
   versus-slice-note placement in plan mode. Do not reopen the choice.

7. The drift-guard mechanism. Confirm how the canonical-shape suite reads the
   target `dis-canonical` model's field metadata and how the both-directions
   column-set assertion is built over the mapping-produced columns only. If
   `dis-canonical` does not expose field metadata in a Pandera-consumable form, the
   coupling buys less than it costs; the fallback is a decoupled suite plus the
   same drift test. Re-confirm the derive-from-canonical lean against the actual
   `dis-canonical` surface before fixing it.

8. The failure taxonomy and formatter. Confirm how the three failure types map to
   `dis-core` errors or a typed result object, the tenant-readable reason shape,
   where the source-shape reason ("expected `item_code`, got `itemcd`") is
   produced, and that the formatter logs no PII or raw payloads.

9. Which canonical columns are mapping-produced versus consumer-injected. The
   partial-contribution framing (the engine emits source-owned columns; the
   consumer injects identity, `trace_id`, `mapping_version_id`) rests on knowing,
   per canonical table, exactly which columns the mapping populates and which the
   consumer fills in. Introspect the live canonical schema to draw that line per
   target model, since the drift guard (criterion 6) and the canonical-shape suite
   (criterion 5) both scope to the mapping-produced set. Do not assert the split
   from a snapshot.
