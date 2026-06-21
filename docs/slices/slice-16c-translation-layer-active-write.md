# Slice 16c: translation layer + ACTIVE write (create becomes functional)

Turns the 16a create contract into a stored, working schema. POST /api/v1/mapping-templates
receives {source_id, template_name, template_type, columns:[{src_key, dest_key, +format
declarations}]}, TRANSLATES it to a mapping_rules SourceMapping
(rename/normalize/cast/derive), runs the full semantic validation, and on success WRITES
AN ACTIVE row to config.source_mappings. Single state: no DRAFT, no staging. On any
failure (translation, token, or semantic): a 4xx and nothing is persisted. This is the
slice that makes create functional end to end.

## Depends on

- Slice 16a (the create request contract: MappingTemplateCreate + MappingColumn, the
  synthetic no-write handler 16c replaces).
- Slice 16b (parse_percent op; the widened src_thousand_separator enum). 16c wires these
  into the translator.
- The dis-mapping engine (the op vocabulary + arg shapes), the dis-canonical models (the
  per-column datatype + decimal precision/scale), and the existing semantic validation in
  mapping_validation.py.
- Investigation `docs/scratch/mapping-templates-create-investigation.md` (P1-P4 + the 16c
  discovery: the ACTIVE-write path, the declaration->op mapping, the format-token finding,
  the precision reflection, the test surface).

## Decisions locked

- Single state: create writes ACTIVE on success (no DRAFT, no staging). Any failure -> 4xx,
  nothing persisted. Create is all-or-nothing.
- The request carries DATE format as a friendly token (DD-MM-YYYY), NOT a strptime code.
  16c builds the token->strptime conversion. The accepted token set is exactly five
  (matching the frontend picker, communicated to Amit):
    DD-MM-YYYY, DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD, DD-MM-YY
  An unknown/malformed token -> 4xx. The set is the contract; backend and picker stay in
  lockstep (a new format is added backend-side before the picker offers it).
- Precision/scale for the decimal cast is read INTERNALLY by the translator from the
  dis-canonical model (extend the existing reflection helpers). The catalog wire object
  (TemplateMappingField) is NOT changed -- Amit sees no new fields.
- parse_percent keeps its 16b divide-by-100 semantics. 16c builds NO percent-scale guard:
  the (X,2)-vs-fraction precision concern is deferred and noted (a percentage destination
  should be at least (X,3); if a too-small-scale target surfaces in real use, fix the
  canonical column scale then). No reject/clamp/warn logic in 16c.
- The ACTIVE-write path (create_template) already exists. 16c VERIFIES it writes ACTIVE
  correctly (status, activated_at, version minting, constraints, RLS) and overwrites it
  if not -- it is not trusted blind.

## The flow (handler, replacing 16a synthetic no-write)

1. Parse + shape-validate the request (16a contract, already in place).
2. Translate columns -> a mapping_rules SourceMapping dict:
   - rename: {src_key: dest_key} for every column whose dest_key is a real catalog key;
     __ignore__ columns are excluded (no rename entry).
   - normalize: per column, from the declarations:
     - src_datetime_format present -> parse_date OR parse_datetime, chosen by the target
       column's canonical datatype (date -> parse_date; datetime -> parse_datetime). The
       token is converted to a strptime code via the conversion table. parse_datetime's
       timezone arg: set per the canonical/declaration rule (confirm in plan).
     - src_decimal_separator (+ optional src_thousand_separator) -> parse_decimal with
       {decimal_separator, thousands_separator}. Thousands ABSENT -> explicit null arg
       (the op requires the key, may be null). Applied when the target datatype is a
       number/decimal.
     - src_is_percentage true -> parse_percent with {decimal_separator, thousands_separator}
       (needs the separators too).
   - cast: per column, derived from the target canonical datatype
     (string/integer/decimal/date/datetime/boolean). For decimal, set precision+scale read
     from the dis-canonical model (internal reflection). Non-decimal: type only.
   - derive: empty in 16c (the contract does not express derive; out of scope).
3. Run the semantic gate on the translated rules: validate_mapping_rules_for_type
   (target legality, mandatory coverage, presence pairings). Any failure -> 4xx, no write.
4. On success: mint a fresh template_id (UUIDv7) and call the ACTIVE-write path
   (create_template) -> writes the row status=ACTIVE, activated_at set, version minted,
   RLS-scoped.
5. Return the real MappingTemplateDetail built from the written row (not synthetic).

## Format-token conversion table (16c builds)

A token->strptime map covering exactly the accepted set:
  DD -> %d   MM -> %m   YYYY -> %Y   YY -> %y   (separators "-" and "/" preserved)
So: DD-MM-YYYY -> %d-%m-%Y ; DD/MM/YYYY -> %d/%m/%Y ; MM/DD/YYYY -> %m/%d/%Y ;
    YYYY-MM-DD -> %Y-%m-%d ; DD-MM-YY -> %d-%m-%y
A token outside the accepted set -> a translation/validation error -> 4xx. (Where this
table lives -- a small util in dis-ui-server -- decide in plan.)

## Verify/fix the ACTIVE-write path

Confirm create_template writes a correct ACTIVE row:
- status=ACTIVE; activated_at set (satisfies ck_csm_activated_at).
- uq_csm_active_per_source satisfied (fresh template_id per create => no prior ACTIVE for
  the triple).
- mapping_version_id (BIGSERIAL) and version_seq_per_source (BEFORE-INSERT trigger) minted
  correctly; first row seq == 1.
- the write is RLS-scoped (rls_session, tenant GUC).
- IntegrityError mapping intact (name conflict -> 409; tenant-FK -> 403).
If any of these is wrong, fix create_template; do not trust it blind.

## Precision/scale reflection (internal)

Extend the field-catalog reflection helpers (_structural / the Annotated peelers) to also
read max_digits / decimal_places off the dis-canonical Field metadata, so the translator
can build CastSpec(type="decimal", precision=P, scale=S) per decimal dest_key. Keep this
internal to the translator/builder; do NOT add precision/scale to TemplateMappingField
(the catalog wire object stays unchanged). Reachable in-process (no DB round-trip).

## Scope

In: the translator (columns + declarations -> mapping_rules); the format-token conversion
table; the internal precision/scale reflection for the decimal cast; the handler rewire
(translate -> validate -> ACTIVE-write -> real detail); verify/fix create_template; the
test surface below.

Out (with where each lands):
- The percent-scale guard -- deferred, noted (no logic in 16c).
- derive translation -- the contract does not express derive; out.
- The observable end-to-end walkthrough (realistic POST -> schema -> CSV -> canonical) --
  Slice 16d.
- Any catalog wire-object change (precision/scale stays internal).
- Any services/dis-ui edit (Amit's; READ-ONLY). The picker token change is Amit's, already
  communicated; not in this slice.

## Test surface

Unit (dis-ui-server):
- Happy-path translation: a snapshot columns request -> the expected SourceMapping
  (rename + normalize ops + cast with correct precision/scale + the right parse op per
  datatype). Assert the produced dict matches the expected mapping_rules.
- Token conversion: each of the 5 accepted tokens -> the correct strptime code; an unknown
  token -> 4xx.
- Thousands-absent -> explicit null thousands_separator arg in the produced op.
- parse_percent wiring: src_is_percentage true -> parse_percent op with the separators.
- Cast precision: a decimal dest_key -> CastSpec with the canonical column's precision/scale.
- Semantic rejection (translated rules fail the gate, 4xx, no write): bad dest_key
  (target legality), missing mandatory field (mandatory coverage), broken presence pairing
  (presence pairings). Each asserts a 4xx and no DB call.

Integration (dis-ui-server, live stack) -- restore the 5 skipped tests as ACTIVE-write:
- test_create_writes_a_valid_draft_with_a_minted_uuid7 -> flip to ACTIVE: row status=ACTIVE,
  activated_at non-NULL, active_version==1, draft_version is None, version_seq==1, and the
  translated mapping_rules round-trips to the expected document. (Rename the test to _active_.)
- test_duplicate_template_name_is_a_clean_409 -> real write -> 409 on dup name/source.
- test_same_name_under_another_source_is_fine -> two ACTIVE writes under different sources.
- test_non_uuid_token_sub_creates_with_null_created_by -> ACTIVE write, NULL authorship.
- test_well_formed_unknown_tenant_reads_empty_writes_403 -> real write attempt -> 403.
- Invert/remove test_create_persists_nothing_against_the_live_stack (the 16a no-write test;
  it now contradicts ACTIVE-write).

## Acceptance criteria

- POST with a valid snapshot columns body -> 201, a real ACTIVE row in
  config.source_mappings (status ACTIVE, activated_at set, version_seq 1), and the response
  is the real MappingTemplateDetail (active_version 1, draft_version None).
- The stored mapping_rules is the correctly translated SourceMapping (rename + the right
  normalize ops with converted strptime formats + cast with the canonical precision/scale).
- A request with an unknown date token, a bad dest_key, a missing mandatory field, or a
  broken presence pairing -> 4xx, nothing persisted.
- A duplicate (source, name) -> 409; an unprovisioned tenant -> 403.
- create_template verified to write ACTIVE correctly (or fixed).
- The catalog wire object (TemplateMappingField) is unchanged.
- The 5 skipped integration tests restored (DRAFT->ACTIVE where applicable); the no-write
  test inverted/removed.
- make check / lint / mypy clean; tests in the same commit. services/dis-ui untouched.

## Open questions for plan mode

1. Where the translator lives (a new module in dis-ui-server vs extending an existing one)
   and where the token table lives. Lean: a dedicated translator module + a small token
   util, both in dis-ui-server.
2. parse_datetime timezone arg: what the translator sets it to (the declaration does not
   carry timezone; the canonical datetime columns are UTC). Confirm the rule -- likely a
   fixed UTC/null per the column -- in plan, grounded in how parse_datetime + the canonical
   datetime columns already behave.
3. Whether any normalize op is needed for plain string columns (e.g. normalize_whitespace)
   or whether string columns get rename + cast(string) only. Lean: rename + cast only, no
   normalize unless a declaration calls for it; confirm against mandatory-coverage.
4. parse_date vs parse_datetime selection: confirm the target-datatype lookup is the sole
   selector and that a date column never receives a datetime op (and vice versa).
