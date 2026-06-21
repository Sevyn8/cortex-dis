# libs/dis-mapping — Claude Code Context

Loaded when Claude Code works in `libs/dis-mapping/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

The pure mapping engine (slice-05). `apply_mapping(mapping, chunk)` applies one source's
`mapping_rules` in the mandatory `rename -> normalize -> cast -> derive` order (D20) over a
parsed in-memory Polars frame and returns a **partial canonical contribution**: the
source-owned, mapping-produced columns only, plus per-cell typed failures.

For interfaces, types, file structure, see `README.md` in this directory.

## Rules specific to this lib

- **Pure. No I/O of any kind at runtime** — no Postgres, GCS, Pub/Sub, network, file reads.
  D4's runner-swap guarantee rests on this. Held by import-linter contracts (root pyproject),
  the no-I/O contract test (`tests/contract/test_pipeline_purity.py`), and review.
- **Partial contribution, never a full row.** The engine NEVER populates `tenant_id`,
  `store_id`, `trace_id`, or `mapping_version_id` — consumer-injected after it runs
  (D8, hard rule 5). It emits exactly the mapping's target columns (rename + derive).
- **Ordered transform lists.** `normalize`/`derive` map a column to an ORDERED LIST of atomic,
  single-purpose ops applied in declared sequence; an empty list is a valid no-op. A cell
  failing at step *k* reports that step's `op` + `transform_index` and skips the rest.
- **Per-cell failures are data, not exceptions**; a row with any failed cell yields NO
  contribution (whole-row drop). No pass-threshold, no routing — B2 is the consumer's
  (Slice 10). Exceptions (`MappingConfigError`/`MappingInputError`, from dis-core) are for
  invalid config or caller-contract violations only, raised at construction where decidable.
- **Locale/format is asserted by the mapping, never inferred.** `parse_decimal`'s
  `decimal_separator` AND `thousands_separator`, and `parse_integer`'s
  `thousands_separator`, are mandatory declarations (explicit `null` = "none"); a missing
  declaration fails at `SourceMapping` construction. This rule has no other doc home (D49).
- **`normalize` is the live field name** — not `transforms` (D49). The inner `mapping_rules`
  shape is DEFINED by `SourceMapping` (live data does not constrain it); Slice 14 onboarding
  generates against it.
- Mapping config is read-only here; loading from `config.source_mappings` is the consumer's
  side-input (Slice 10). Authoring lives in dis-ui-server onboarding.
- The named-custom-transform escape hatch is DEFERRED (no registry code; slice-05 scope
  boundary). A gap the bounded vocabulary cannot express is a vocabulary extension or an
  onboarding problem, not a per-source code path.
- Depends on `dis-core` + `polars` (pinned `>=1.41,<2.0`, same pin as dis-validation) only.
  Never dis-canonical, dis-validation, dis-rls, dis-pii, dis-storage, dis-testing.
- Never log a cell value (PII discipline); failure objects carry values for quarantine only.

## References

- `README.md` (this directory) — interface and structure.
- Root `CLAUDE.md` — project-wide invariants. `decisions.md` D20, D49.
