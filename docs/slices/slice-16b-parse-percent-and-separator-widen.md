# Slice 16b: parse_percent op + thousands-separator enum widen

Two small, independent items that complete the engine + contract surface the new
create flow needs. (1) Add the one missing normalize op, parse_percent, so a
percentage source column can be expressed. (2) Widen the src_thousand_separator
request enum to include "." so European-format numbers (1.234,56) are declarable.
No translation layer, no DB write, no create-flow wiring (that is 16c). This slice
makes the engine and the contract complete; 16c consumes them.

## Depends on

- Slice 16a (the create request contract: MappingColumn with the four format
  declarations, including src_thousand_separator). 16b widens one of its enums.
- The dis-mapping engine (normalize stage, the NORMALIZE_IMPLS registry, the
  NORMALIZE_OPS frozenset, validate_normalize_args, the registry-vocabulary parity
  test). 16b adds one op via the established four-point pattern.
- Investigation `docs/scratch/mapping-templates-create-investigation.md` (P4: the op
  dispatch mechanism and the four-point add cost) and
  `docs/scratch/mapping-engine-ops-inventory.md` (the full existing op set; parse_percent
  confirmed the only missing op).

## Decisions locked

- parse_percent semantics: strip a trailing "%" if present, parse the numeric body using
  declared separators, then DIVIDE BY 100. "12.5%" -> "0.125". This is the mathematically
  correct representation (a percentage is the fraction). Output is a normalized string
  (the normalize stage is string->string); the cast stage converts it to the canonical
  decimal type afterwards.
- parse_percent takes the same separator args as parse_decimal (decimal_separator
  required, thousands_separator required key, may be explicit null). It is self-contained:
  it does its own numeric parsing + the /100, not a composition that runs after
  parse_decimal. Consistent with how parse_decimal/parse_integer own their parsing.
- Failure behaviour matches the numeric parse ops: a non-null cell whose body does not
  match the locale-aware numeric pattern is nulled and recorded as a
  CellNormalizationFailure. The "%" suffix is optional (a column may be all bare numbers
  the user has declared as percentages); presence/absence of "%" is not itself a failure.
- src_thousand_separator widens from {",", "'"} to {".", ",", "'"}. src_decimal_separator
  unchanged ({".", ","}). This matches the three target locales: US 1,234.56, EU 1.234,56,
  Swiss 1'234.56. Already communicated to the frontend (the NOTE in the create-flow probe
  doc) as landing in this push.

## Scale caveat (verify, do not assume)

parse_percent outputs the divided fraction (12.5% -> 0.125), which needs scale >= 3 to
avoid truncation; 12.567% -> 0.12567 needs scale >= 5. The canonical column a percentage
maps to must have enough scale. The precision probe found expiry_confidence is
numeric(3,2) (holds only 2 decimals). BEFORE finalizing, confirm which canonical
column(s) src_is_percentage / parse_percent targets and that their scale holds the
divided value without truncation. If a target column has insufficient scale, that is a
canonical-schema question to surface (not silently truncate) — flag it; do not change the
schema in 16b.

This is a real correctness check: a percent stored into numeric(3,2) silently loses
precision (0.125 -> 0.12). The slice must confirm the target scale is adequate or surface
the mismatch.

## Goal

After 16b: the engine has a parse_percent normalize op (added via the four-point pattern,
pinned by the parity test), and the create request accepts "." as a thousands separator.
Both are inert until 16c wires the translator — 16b adds capability + contract surface,
not behaviour in the create path.

## Task

Plan-mode first. Two items:

### Item 1 — parse_percent (four-point add, all in libs/dis-mapping)

1. Impl function: add the parse_percent implementation in engine/normalize.py, modelled
   on parse_decimal (validate the numeric body against the locale-aware regex built from
   declared separators, strip thousands, rewrite decimal to ".", strip an optional
   trailing "%", divide by 100). Output a normalized decimal string. Null + record failure
   on non-matching non-null cells.
2. Registry entry: add "parse_percent" -> impl to NORMALIZE_IMPLS.
3. Vocabulary: add "parse_percent" to the NORMALIZE_OPS frozenset.
4. Arg validator: add the parse_percent branch in validate_normalize_args (same required
   args as parse_decimal: decimal_separator, thousands_separator-as-required-key).

The registry<->vocabulary parity test must stay green (it pins steps 2 and 3 together).

### Item 2 — widen src_thousand_separator (dis-ui-server request model)

In the 16a MappingColumn model (schemas/mapping_templates.py), change
src_thousand_separator from Literal[",", "'"] to Literal[".", ",", "'"]. No other field
changes. Update the 16a unit tests that asserted "." was rejected (the malformed-declaration
test used a now-valid value or a still-invalid one — adjust so the test still proves the
closed set rejects a genuine non-member, e.g. ";").

### Plan-mode grounding (file:line before building)

- The parse_decimal impl + its arg validator (the template for parse_percent), the
  NORMALIZE_IMPLS registry, the NORMALIZE_OPS frozenset, and the parity test.
- The 16a MappingColumn src_thousand_separator Literal and the unit test(s) asserting the
  separator value set.
- Confirm (scale caveat): which canonical column(s) a percentage targets and their scale.

## Scope

In: the parse_percent op (impl + registry + vocab + arg-validator) and its tests; the
src_thousand_separator enum widen and its test adjustment.

Out (with where each lands):
- Any use of parse_percent in the create flow / translation of src_is_percentage ->
  parse_percent — Slice 16c.
- The translation layer, DB write, ACTIVE persistence — Slice 16c.
- Reading decimal precision/scale from the dis-canonical model — Slice 16c.
- Any canonical-schema change if a percent target has insufficient scale — surfaced here,
  decided separately.
- Any services/dis-ui edit (Amit's; READ-ONLY).

## Acceptance criteria

- parse_percent is in NORMALIZE_IMPLS and NORMALIZE_OPS; the parity test passes.
- parse_percent("12.5%") with US separators -> "0.125"; with EU separators
  ("," decimal, "." thousands) "12,5%" -> "0.125"; a bare "12.5" (declared percentage,
  no "%") -> "0.125"; a non-numeric cell -> null + recorded failure. (Unit tests, on the
  op directly.)
- The arg validator requires decimal_separator and the thousands_separator key for
  parse_percent (mirrors parse_decimal); a missing required arg -> MappingConfigError at
  SourceMapping construction.
- The create request accepts src_thousand_separator "." (a snapshot column with EU
  declaration shape validates); a non-member value (e.g. ";") still 422s.
- Scale caveat resolved: the percent target column(s) confirmed to hold the divided
  fraction without truncation, OR the mismatch surfaced as an open item.
- make check / lint / mypy clean; tests in the same commit. services/dis-ui untouched.

## Open questions for plan mode

1. parse_percent and a trailing "%": should "%" be required, optional, or stripped-if-present?
   Lean optional/stripped-if-present (a column declared as percentage may store bare
   numbers or "12.5%" interchangeably; the declaration, not the glyph, marks it a percent).
   Confirm in plan.
2. Negative percentages: does the locale numeric body already allow a leading sign (parse_decimal
   does)? parse_percent should inherit the same sign handling. Confirm.
3. Scale caveat: if the percent target column scale is insufficient (e.g. numeric(3,2)
   for a 0.12567 value), surface it — do not truncate, do not change the schema in 16b.
   State the finding in the plan.
