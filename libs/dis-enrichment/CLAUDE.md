# libs/dis-enrichment — Claude Code Context

Loaded when Claude Code works in `libs/dis-enrichment/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

The pure enrichment engine (slice-5b). `apply_enrichment(contribution, facts, table=...)`
overwrites the registered canonical fields for a table with the authoritative internal-source
values the consumer hands in — the lib's value **wins over the mapping** (D95). It runs after
the mapping engine and before post-validation (D94), so enriched values pass the same
canonical-shape gate. First registered fields: `currency` + `tax_treatment`, current-position
table only.

For interfaces, types, file structure, see `README.md` in this directory.

## Rules specific to this lib

- **Pure. No I/O of any kind at runtime** — no Postgres, GCS, Pub/Sub, network, file reads.
  The same pure-lib / consumer-does-I/O split as `dis-mapping`. Held by the import-linter
  forbidden-modules contract (root pyproject), the purity test (`tests/unit/test_pure_imports.py`),
  the no-I/O contract test (`tests/contract/test_pipeline_purity.py`), and review.
- **The lib reads nothing.** The consumer resolves the values from the authoritative internal
  source and hands them in as `facts`; the registry merely RECORDS the source/field (mechanism,
  not policy — business rules arrive as handed-in data, never baked as literals).
- **Source-agnostic identity.** `identity_mirror.stores` is the first source wired, NOT the
  lib's definition. Naming, the registry shape, and the interface must not bake "store" in.
- **Output wins, explicitly.** `apply_enrichment` uses `with_columns`, which replaces a
  same-named column, so a mapping-produced value of a registered field cannot survive. Column
  order/row count/row alignment are preserved (column-wise mutation only — never row filtering).
- **Lookup only.** The lib never computes or derives attributes from accumulated data (velocity,
  stock age, cost trend); those are the `daily-compute` service, out of scope forever.
- **Failure posture.** A registered field MISSING from `facts` is a caller-contract violation →
  `EnrichmentError` (loud, code-quality rule 4). A registered field PRESENT-BUT-BLANK is written
  through as-handed-in this slice; the loud-fail-on-blank guard is D97 (deferred, not built).
- Depends on `dis-core` + `polars` (pinned `>=1.41,<2.0`, same pin as dis-mapping/dis-validation)
  only. Never dis-mapping, dis-validation, dis-canonical, dis-rls, dis-pii, dis-storage,
  dis-audit, dis-quarantine, dis-testing. Never log a resolved value.

## References

- `README.md` (this directory) — interface and structure.
- Root `CLAUDE.md` — project-wide invariants. `docs/slices/slice-5b-enrichment-lib.md`;
  `decisions.md` D94, D95, D96, D97, D98.
