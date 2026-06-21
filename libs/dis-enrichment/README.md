# `libs/dis-enrichment/`

The pure enrichment engine (slice-5b). Used by the streaming consumer on the snapshot /
current-position write path (Slice 5b): after `dis-mapping` produces its contribution and
before post-validation, the consumer reads the authoritative internal source, hands the facts
in, and the engine overwrites the registered canonical fields — the lib's value wins over the
mapping (D95). Like `dis-mapping`, the engine has no I/O, so the consumer (or a future Beam
runner) wraps it unchanged.

```
libs/dis-enrichment/
├── pyproject.toml              # deps: dis-core, polars>=1.41,<2.0 (pinned; same as dis-mapping/dis-validation)
├── README.md
├── CLAUDE.md
├── src/
│   └── dis_enrichment/
│       ├── __init__.py         # public API: apply_enrichment, EnrichmentResult, enrichment_fields, EnrichmentField, CURRENT_POSITION
│       ├── py.typed
│       ├── result.py           # EnrichmentResult (frozen): contribution + enriched_columns
│       ├── registry.py         # EnrichmentField + ENRICHMENT_REGISTRY + enrichment_fields(table) — the swappable seam
│       └── engine/
│           └── apply.py        # apply_enrichment: broadcast/overwrite registered columns, output-wins
└── tests/
    └── unit/
```

## The engine surface

```python
apply_enrichment(
    contribution: pl.DataFrame,
    facts: Mapping[str, Any],     # resolved {canonical_field -> value}, handed in by the consumer
    *,
    table: str,                   # table-scope token, e.g. CURRENT_POSITION
    log_context: LogContext | None = None,
) -> EnrichmentResult
```

Frame-in/frame-out and pure. For each registered field whose `tables` includes `table`, the
engine overwrites (or creates) that column with the broadcast `facts[field]` value across every
row. `EnrichmentResult.contribution` has the SAME row count and SAME row order as the input
(column-wise mutation only — the consumer reuses its `MappingResult.source_row_indices`
unchanged); `enriched_columns` names the columns touched.

- A registered field MISSING from `facts` → `EnrichmentError` (consumer-contract violation).
- A registered field PRESENT-BUT-BLANK → written through as-handed-in (D97 loud-fail deferred).
- A table with no registered fields → no-op (`enriched_columns == ()`), used to keep the event
  path unaffected even though the seam sits upstream of the snapshot/event branch.

## The registry (D95)

`ENRICHMENT_REGISTRY` is a code-owned tuple of `EnrichmentField(canonical_field, source,
source_field, tables)` behind the `enrichment_fields(table)` seam (a config table can replace
the declaration later without touching the resolution logic). The `source`/`source_field` are
recorded for the consumer's read; the lib itself never reads them. Source-agnostic:
`identity_mirror.stores` is the first source, not the lib's identity. Only `CURRENT_POSITION`
is wired this slice; the `tables` flag admits the event/history tables later.
