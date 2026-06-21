# libs/dis-pii — Claude Code Context

Loaded when Claude Code works in `libs/dis-pii/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

PII detection and a fail-loud gate, as pure functions over a caller-supplied source
mapping. PII tokenization happens at the receiver before persistence (hard rule 2);
the real tokenizer/keys/backend are deferred — this lib v1.0 only *detects* PII and
*refuses loudly* when it has nowhere safe to put it.

For interfaces, types, file structure, see `README.md`.

## Rules specific to this lib (Slice 4)

- **DB-free, crypto-free, network-free.** Depends on `dis-core` ONLY — never `dis-rls`,
  never a DB, never real crypto. Pure functions over the mapping argument.
- **The gate never has an off switch.** `assert_pii_handled` raises
  `PiiBackendNotConfiguredError` whenever a flagged PII column has no configured
  backend. The not-raise branch is reachable ONLY by an explicitly injected backend.
  No config default or flag may disable the gate (hard rule 2, code-quality rule 4).
- **Detection is heuristic (field-name / pattern) — it has false negatives.** A PII
  column whose name the matcher does not recognise is not detected, so the gate does
  not fire on it. Do not present detection as exhaustive. There is no explicit
  per-column PII flag in the schema/`mapping_rules` to read; adding one is a future
  schema + contract change. See `decisions.md` D40.
- **Never log or embed a raw PII value** in an error, log line, or message — column
  *names* only.
- **`tokenizer.py` / `key_vault.py` / `policy.py` are inert seams.** Import-safe, no
  I/O; methods raise `NotImplementedError`. They mark where the real implementation
  lands (mirroring the Slice 3 `BqClient` stub). Do not flesh them out here.

## References

- `README.md` — interface and structure.
- Root `CLAUDE.md` — project-wide invariants. `decisions.md` D24 (PII tokenization), D40 (posture OPEN).
