# `libs/dis-canonical/`

Pydantic models and dataclasses representing canonical schema rows. Source-of-truth Python representation of `current_store_positions` and history tables.

```
libs/dis-canonical/
├── pyproject.toml
├── README.md
├── src/
│   └── dis_canonical/
│       ├── __init__.py                 # exports the 4 models
│       ├── hot/
│       │   └── store_sku_current_position.py   # StoreSkuCurrentPosition
│       ├── events/
│       │   ├── sale_events.py          # StoreSkuSaleEvent
│       │   └── change_events.py        # StoreSkuChangeEvent
│       ├── signals/
│       │   └── signal_history.py       # StoreSkuSignalHistory (no mapping_version_id)
│       └── shared/
│           ├── identifiers.py          # re-export dis-core UUID-key aliases
│           ├── enums.py                # tax_treatment, expiry_source, CHECK vocabs
│           ├── types.py                # varchar(n)/char(3)/numeric(p,s) aliases
│           └── base.py                 # CanonicalModel (extra="forbid")
└── tests/
    └── unit/
```

One model per `canonical.*` base table (four), hand-aligned to the **live**
`ithina_dis_db` schema (introspected, not from the DDL files). Layout splits hot /
events / signals to reinforce the three table classes (merge-upsert vs append-only
vs daily-compute). `dis-canonical` depends on `dis-core` only for the identifier
aliases.

**Why this lib exists separately from schemas/.** `schemas/canonical/` holds SQL DDL and dbt models (source of truth for storage). `libs/dis-canonical/` holds Python models (source of truth for in-memory representation). Both must agree. Models are **hand-aligned** to the live schema today (introspection-driven codegen via `tools/codegen/` is deferred — that generator does not exist yet); drift is guarded by inline evidence comments citing the introspected column. Splitting by usage (SQL vs Python) is cleaner than co-locating.

**Why `hot/` and `history/` subdirs.** The two tiers have different semantics (merge upsert vs append) and different fields (history has full event metadata; hot has aggregated state). Splitting at the lib level reinforces the architectural distinction.

---
