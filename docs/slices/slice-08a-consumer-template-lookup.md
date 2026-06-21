# Slice 8a: consumer template-keyed mapping lookup

The fix owed by D71. The streaming consumer currently selects the mapping by
`(tenant_id, source_id, status='ACTIVE')` and takes `.first()`, with no `template_id`
predicate. Since 14a the live `uq_csm_active_per_source` index permits multiple ACTIVE rows
per `(tenant, source)`, one per template (D68), so the lookup is template-unaware: deterministic
today only because the 0005 backfill leaves exactly one ACTIVE 'default' template per source.
The moment a second template goes ACTIVE under one source, `.first()` returns an arbitrary row
and a CSV is processed with the wrong template's `mapping_rules`, silently, no error. Slice 8
carried `template_id` end to end on the wire (it is on `ingress.ready`, parsed-but-unused on
the consumer envelope); this slice wires it into the lookup so the right template's rules are
applied.

Small, surgical, consumer-only. The fix is a single predicate plus threading the field that
is already on the envelope. The slice closes D71's hard gate: once it lands, a promote-to-ACTIVE
path that can produce a second ACTIVE template per source becomes safe to build.

## Depends on

- Slice 8 (built/pushed): `template_id` is required on `ingress.ready` (the contract) and is
  already parsed on the consumer's envelope model as a parsed-but-unused field, with the
  regression pin `test_mapping_lookup_stays_template_unaware_until_slice_8a` that fails the
  moment `template_id` enters `pipeline/mapping.py`. This slice deliberately makes it appear,
  so that pin is retired/updated here (it has done its job).
- Slice 14a (built/pushed): the template grain (D68) and the live `uq_csm_active_per_source`
  index `(tenant_id, source_id, template_id) WHERE status='ACTIVE'`, which makes the
  template-keyed lookup return at most one row genuinely (not by `.first()` luck).
- Slice 10 (built/pushed): the streaming consumer, `load_active_mapping`, and the existing
  `ingress.ready` envelope handling that already reads `source_id` and cross-checks it against
  the GCS path + bronze row. This slice extends that same path with `template_id`.
- Decisions in force: D71 (this is the owed fix; the hard gate this slice lifts), D68 (template
  grain), D69 (config RLS), D22 (`mapping_version_id` pin, unchanged), D54 (the consumer trusts
  `ingress.ready`, resolves no identity), D33/D65 (the dedup key, untouched by this slice).
- Decision to REGISTER (operator assigns the number at the commit gate): the consumer's
  behaviour when `ingress.ready` carries no `template_id` (the D71 open item; see Task 0 — CC
  first confirms whether absent is even reachable given Slice 8 made the field required, which
  decides whether this is a real policy or a documented impossibility).

## Goal

After this slice, the streaming consumer applies the mapping of the exact template the CSV was
uploaded against. `load_active_mapping` keys on `(tenant_id, source_id, template_id,
status='ACTIVE')`, which the `uq_csm_active_per_source` index resolves to at most one row,
removing the `.first()` arbitrariness. `template_id` is read off `ingress.ready` (already on
the envelope), threaded through orchestration into the lookup, and recorded on the
`MAPPING_LOOKED_UP` audit event. D71's hard gate is satisfied: a second ACTIVE template per
source no longer causes a silent wrong-mapping.

## Task

Amend the consumer only. Confirm the live shapes in plan mode rather than trusting the D71 line
references (the repo moved since D71 was written; verify `load_active_mapping` and the envelope
against the current code). Decompose in plan mode and show:

0. **Plan-mode grounding (ERROR, not skip).**
   - The live `load_active_mapping`: its current query, what it keys on, that it takes
     `.first()`, where it lives (verify the path/lines; D71's `mapping.py:188-194` predates the
     Slice 8 rebase). Confirm the live `uq_csm_active_per_source` index is
     `(tenant_id, source_id, template_id) WHERE status='ACTIVE'` so the keyed lookup is
     genuinely single-row.
   - The consumer envelope: confirm `template_id` is present as a parsed field (from Slice 8)
     and is currently unused, and locate the regression pin that guards `mapping.py`.
   - **The `template_id`-absent question (the one real decision).** Slice 8 made `template_id`
     REQUIRED on `ingress.ready`. Confirm whether a message without it is therefore structurally
     impossible (it would fail the envelope parse before reaching the lookup). If absent is
     unreachable, the back-compat-fallback-vs-hard-error question is moot and the slice documents
     why (no fallback code, the contract guarantees presence). If absent IS reachable (state how
     — an old in-flight message, a future non-CSV producer), state the policy: hard error
     (`MappingConfigError`) vs a single-ACTIVE fallback, with reasoning. Recommend.
   - The `MAPPING_LOOKED_UP` audit event: confirm its current `event_data` shape and that adding
     `template_id` is additive.

1. **The lookup fix.** Add `AND template_id = :template_id` to `load_active_mapping`; the
   index then guarantees at most one row. State what happens on no-match now (the same
   `MappingConfigError` path the current absent-mapping case raises, but now "no ACTIVE mapping
   for this template" rather than "for this source"). Confirm `mapping_version_id` is still
   stamped on canonical rows exactly as before (D22 unchanged).

2. **Threading.** Read `template_id` off the envelope (already parsed), thread it through
   orchestration into `load_active_mapping`, and add it to the `MAPPING_LOOKED_UP` audit
   `event_data` alongside the existing `source_id`/`mapping_version_id`.

3. **Retire the Slice 8 pin.** `test_mapping_lookup_stays_template_unaware_until_slice_8a`
   is designed to fail when `template_id` enters `mapping.py`. Update/retire it as part of this
   slice (it has served its purpose); replace it with a test asserting the lookup IS now
   template-keyed (the inverse property).

4. **Tests (same commit).** The core new property: two ACTIVE templates under one source, an
   `ingress.ready` naming each → each resolves to ITS OWN template's `mapping_rules` (not
   `.first()` luck); prove by seeding two distinct ACTIVE templates with distinguishable rules
   and asserting the applied mapping matches the named `template_id`. A `template_id` naming no
   ACTIVE row → the clean `MappingConfigError` (not a silent wrong-mapping). The
   `template_id`-absent behaviour per the Task 0 finding (if reachable, the chosen policy is
   tested; if unreachable, a test proves the envelope rejects an absent-`template_id` message).
   `mapping_version_id` still stamped unchanged. The dedup path (D33/D65) untouched, prove a
   redelivery still dedups.

## What this slice does NOT do

No promote/reject/shadow path (that is the next slice, now UNBLOCKED by this one). No change to
the `ingress.ready` or `csv.received` contracts (Slice 8 already made `template_id` required;
this slice only consumes it). No change to dis-ui-server, the worker, bronze, or the dedup key
(D33/D65). No change to `mapping_version_id` or any canonical FK (D22). No edit to
`services/dis-ui` (READ-ONLY). No DDL.

## Open questions for plan mode

1. The `template_id`-absent policy: first whether absent is reachable given Slice 8's required
   field; then, only if reachable, hard-error vs fallback (CC recommends).
2. The exact no-match error message/shape for "no ACTIVE mapping for this template" vs the
   current "for this source" (CC states it).

## Acceptance criteria

- `load_active_mapping` keys on `(tenant_id, source_id, template_id, status='ACTIVE')`; the
  index guarantees at most one row; `.first()` arbitrariness removed.
- Two ACTIVE templates under one source each resolve to their own `mapping_rules`
  (test-proven, the core D71 property).
- A `template_id` with no ACTIVE row → clean `MappingConfigError`, never a silent wrong-mapping.
- The `template_id`-absent behaviour is resolved per the Task 0 finding (policy tested, or
  impossibility proven by the required-field envelope reject).
- `template_id` is on the `MAPPING_LOOKED_UP` audit `event_data`.
- `mapping_version_id` is still stamped on canonical rows unchanged (D22); the dedup path
  (D33/D65) still dedups a redelivery.
- The Slice 8 regression pin is retired and replaced by the inverse (lookup IS template-keyed).
- D71's hard gate is satisfied: the entry can move toward RESOLVED, and the promote-to-ACTIVE
  slice is unblocked.
- `services/dis-ui` unmodified; no DDL; no contract change.
- `make check` / lint / mypy clean; tests in the same commit.
