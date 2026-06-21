# `libs/dis-mapping/`

The pure mapping engine (slice-05). Used by the streaming consumer (Slice 10, to apply
mappings at runtime) and by `dis-ui-server`'s onboarding sub-module (Slice 14, to dry-run
proposed mappings). Both wrap the same pure function — that is D4's runner-swap guarantee:
the engine has no I/O, so a future Beam DoFn wraps it unchanged.

```
libs/dis-mapping/
├── pyproject.toml              # deps: dis-core, polars>=1.41,<2.0 (pinned; same pin as dis-validation)
├── README.md
├── CLAUDE.md
├── src/
│   └── dis_mapping/
│       ├── __init__.py         # public API: apply_mapping, SourceMapping, MappingResult, ...
│       ├── result.py           # MappingResult, CellNormalizationFailure (LogContext re-export)
│       ├── models/
│       │   ├── source_mapping.py   # the mapping_rules contract (see below)
│       │   └── transform.py        # TransformSpec/CastSpec + the bounded vocabulary + arg validation
│       └── engine/
│           ├── apply.py        # apply_mapping: the four-sub-stage composition
│           ├── rename.py       # source names -> canonical names (selects declared columns only)
│           ├── normalize.py    # op impls + ordered per-column transform lists (str -> canonical str)
│           ├── cast.py         # canonical str -> target dtype (after normalize; D20 order is load-bearing)
│           └── derive.py       # generator + optional string ops, over the typed frame
└── tests/
    └── unit/
```

## The engine surface

```python
apply_mapping(mapping: SourceMapping, chunk: pl.DataFrame, *, log_context=None) -> MappingResult
```

Per-chunk and Polars-native. Returns a **partial canonical contribution**
(`MappingResult.contribution`): the source-owned mapping targets only — never
`tenant_id`/`store_id`/`trace_id`/`mapping_version_id` (consumer-injected, D8/hard rule 5) —
plus `source_row_indices` (chunk positions, parallel to contribution rows) and per-cell
`failures`. A row with any failed cell is dropped whole; failures carry
`(row_index, column, value, op, transform_index, expected_format, stage, reason)` at the
grain the quarantine console needs (D20). No threshold, no routing (B2 -> Slice 10).

## The `mapping_rules` contract (D49)

`SourceMapping` defines the inner shape of `config.source_mappings.mapping_rules`
(the live column comment delegates documentation here; live rows' sub-objects are empty):

```jsonc
{
  "version": 1,
  "rename":    {"source_col": "canonical_col"},
  "normalize": {"canonical_col": [{"op": "...", "args": {...}}, ...]},  // ORDERED list
  "cast":      {"canonical_col": {"type": "decimal", "precision": 12, "scale": 4}},
  "derive":    {"canonical_col": [{"op": "copy|constant|date_from_datetime", "args": {...}}, ...]}
}
```

- Ops are atomic and single-purpose; a column's list applies in declared order; `[]` is a
  valid no-op. Vocabulary: `parse_date`, `parse_datetime`, `parse_decimal`, `parse_integer`,
  `parse_boolean`, `map_enum`, `null_tokens`, `normalize_whitespace`, `normalize_case`.
- **Locale rule:** `parse_decimal`'s two separators and `parse_integer`'s
  `thousands_separator` are mandatory declarations (explicit `null` = "no thousands
  separator"); construction fails loud on omission. Formats are asserted, never inferred.
- Derive is bounded to the same declarative vocabulary: a generator first (`copy`,
  `constant`, `date_from_datetime` — e.g. `event_date` from the source timestamp's UTC
  date), then optional normalize ops on string intermediates (composition typed at
  construction). Arbitrary derive logic is the DEFERRED escape-hatch seam (slice-05 scope
  boundary; no registry code exists).

## Why Polars (stack rationale, recorded here per slice-05 OQ6)

Strict dtypes including `Decimal` (canonical numerics never round-trip through float),
vectorized columnar ops matching the per-chunk engine surface, no pandas index semantics to
fight, first-class `pandera.polars` support for the downstream suites, and Arrow interop for
a future Beam runner. Consequence: the engine operates on a chunk, a column at a time —
per-row purity (D4's `(mapping, raw_row)`) holds at chunk grain.
