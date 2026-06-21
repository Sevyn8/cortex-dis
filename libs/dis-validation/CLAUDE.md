# libs/dis-validation — Claude Code Context

Loaded when Claude Code works in `libs/dis-validation/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

Pandera-based validation (slice-05): the source-shape (pre-mapping) and canonical-shape
(post-mapping) suites (D18/D21), the column-provenance registry + drift guard, and the
failure formatter. Pure: takes data and a definition handed in by the caller; returns pass
or typed failures.

For interfaces, types, file structure, see `README.md` in this directory.

## Rules specific to this lib

- **Pure. No I/O at runtime; no DB access ever.** Suite loading from
  `config.source_mappings` is the consumer's side-input (Slice 10). Existence checks
  against `identity_mirror` do NOT belong here — they are the consumer's at write time.
- **The canonical-shape suite scopes to the source-owned, mapping-produced columns** of ONE
  named dis-canonical model — never the consumer-injected columns (identity, `trace_id`,
  `mapping_version_id`). Field set / dtype / nullability / max-length / enum vocab are
  DERIVED from the model's `model_fields`; business invariants are authored. `strict=True`.
- **The provenance registry (`provenance.py`) is the mapping-produced line** (slice-05 OQ9,
  drawn from the live schema). It explicitly partitions every mapping-fed model's fields
  four ways; `assert_no_drift` checks the partition both directions and ERRORS, never skips.
  A new canonical column MUST be classified here or every suite build fails.
  `store_sku_signal_history` raises by design (daily-compute output; D22/D31/D32).
  Judgment-grade entries are marked inline: `yesterday_retail_price` (compute-owned, owner
  Slice 18) and `event_date` (derive, owner Slice 10) are operator-confirmed; `currency` has
  NO live comment and is flagged unconfirmable (kept mapping-produced; owner Slice 10).
  `tax_treatment` ("Denormalized from store") and the change-event `numeric_value_*`
  shortcuts ("Populated by the streaming consumer") are consumer-injected per live comments.
- **The three failure types stay distinct (D18/D20):** source-shape and canonical-shape
  failures originate here; normalization failures originate in dis-mapping and are never
  re-detected or re-formatted here.
- **Pandera is PINNED (`>=0.31,<0.32`); do NOT upgrade without re-testing all tenant
  suites.** Polars pinned `>=1.41,<2.0` (same pin as dis-mapping).
- **D50 workaround in `runner._decimal_dtype_precheck`:** pandera 0.31.1 crashes (raw
  AssertionError) on Decimal-schema vs non-Decimal data. The pre-check is scoped strictly to
  Decimal-schema columns and synthesizes the native failure shape. Removal trigger: the
  canary (`tests/unit/test_pandera_decimal_canary.py`) goes red. Never loosen the canary;
  never catch AssertionError broadly.
- Failure objects may carry offending values (quarantine payload); log lines never do.
  Bind `tenant_id`/`trace_id`/`service`/`stage` via the dis-core logging adapter.
- Depends on `dis-core`, `dis-canonical`, `pandera[polars]`, `polars` only. Never
  dis-mapping, dis-rls, dis-pii, dis-storage, dis-testing (import-linter enforced).

## References

- `README.md` (this directory) — interface and structure.
- Root `CLAUDE.md` — project-wide invariants. `decisions.md` D18, D21, D50.
