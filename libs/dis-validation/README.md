# `libs/dis-validation/`

Pandera suites for the two validation gates (D18/D21), the column-provenance registry +
drift guard, and the failure formatter (slice-05). Used by the streaming consumer (Slice 10)
and `dis-ui-server`'s onboarding sub-module (Slice 14, validation drafts). Pure: definitions
are handed in by the caller; this lib never reads the DB (suite loading from
`config.source_mappings` is the consumer's side-input — note the live
`pre/post_validation_suite_ref` columns are `module:ClassName` references, so definitions
are importable named objects).

```
libs/dis-validation/
├── pyproject.toml              # deps: dis-core, dis-canonical, pandera[polars]>=0.31,<0.32 (PINNED), polars>=1.41,<2.0
├── README.md
├── CLAUDE.md
├── src/
│   └── dis_validation/
│       ├── __init__.py
│       ├── provenance.py       # the mapping-produced vs consumer-injected line (OQ9) + assert_no_drift
│       ├── source_shape.py     # SourceShapeSuiteDef (+ from_rename) + materializer
│       ├── canonical_shape.py  # CanonicalShapeSuiteDef + model-derived materializer (strict)
│       ├── runner.py           # run_source_shape / run_canonical_shape (+ the D50 Decimal pre-check)
│       └── failure_formatter.py# pandera failure cases -> typed, tenant-readable failures
└── tests/
    └── unit/                   # incl. test_pandera_decimal_canary.py — D50 removal trigger
```

## The two suites

- **Source-shape (pre-mapping):** judges a raw chunk in the tenant's vocabulary — required
  source columns (derived from the mapping's rename keys via
  `SourceShapeSuiteDef.from_rename`, handed over as a plain dict), declared per-column
  plausibility (`pattern`), null bounds, row-count bounds. Extra columns tolerated by
  default (D13 permissive posture). Reasons are tenant-readable:
  `"expected column 'item_code', got 'itemcd'"`.
- **Canonical-shape (post-mapping):** judges a mapped contribution against ONE named
  dis-canonical model restricted to the source-owned columns (D8). Field set / dtype /
  nullability / max-length / enum vocab derive from `model_fields`; business invariants
  (range bounds, identifier regex, cross-field checks) are authored on the definition.
  `strict=True` rejects off-universe columns. NO `identity_mirror` existence check — that is
  a DB read the consumer does at write time (Slice 10).

## Provenance + drift guard (criterion 6)

`provenance.py` explicitly partitions each mapping-fed model's columns four ways
(consumer-injected / DB-generated / compute-owned / mapping-produced), with introspected
evidence inline. `assert_no_drift` checks the partition against `model_fields` exactly,
both directions, and ERRORS (`SuiteDriftError`) — never skips. The chain to the live schema
closes through the Slice 3 reconciliation test (dis-canonical vs `information_schema`).
`store_sku_signal_history` raises by design (daily-compute output; D22/D31/D32).

## Failure taxonomy

`SourceShapeFailure` / `CanonicalShapeFailure` (typed results, returned not raised) carry
`(column, check, row_index, value, reason)`; values are quarantine payload, never logged.
Exceptions (`SuiteDefinitionError`, `SuiteDriftError`, from dis-core) are config/drift
errors. Normalization failures belong to dis-mapping and are not re-detected here (D20).
