# Slice 16f: CSV delimiter detection (worker detects -> envelope -> consumer uses)

Fixes a verified split-brain in the ingestion path: the csv-ingest-worker auto-detects the
CSV delimiter during preflight but DISCARDS it, while the streaming-consumer re-reads the
raw CSV with a hardcoded comma. A non-comma file (semicolon, tab, pipe) passes preflight
(the worker sniffs it correctly) then mis-parses downstream into a single mega-column,
failing later with a MISLEADING mapping/validation reason. 16f makes the worker carry the
detected delimiter on the envelope and the consumer parse with it. General: comma,
semicolon, tab, pipe. Quoted fields containing the delimiter are handled by the parser's
standard quote behaviour (verified, not newly built).

## Depends on

- The csv-ingest-worker preflight (DuckDB sniff_csv) and the streaming-consumer CSV read
  (Polars read_csv).
- The csv.received and ingress.ready envelope contracts (both gain a delimiter field).
- Investigation: the delimiter discovery (the split-brain finding, file:line) and
  docs/scratch/csv-ingestion-data-path.md §8 (already documents this gap).

## The bug (grounded)

- Worker preflight: DuckDB sniff_csv auto-detects the dialect (incl. the delimiter), but
  the SELECT pulls only HasHeader, Columns -- the detected Delimiter is discarded
  (csv-ingest-worker preflight, ~preflight.py:69).
- Consumer: pl.read_csv(BytesIO(data), infer_schema=False) -- Polars defaults to comma and
  does not sniff; effectively hardcoded "," (streaming-consumer fetch.py:143).
- Contracts: no delimiter field on the upload request, csv.received, or ingress.ready.
- mapping_rules / template: per-column only (locale: decimal/thousands/datetime). No
  file-structure metadata. Delimiter is a FILE property, not a column property -- it does
  not belong in mapping_rules.
- Failure mode: a ';' file -> worker preflight passes (sniffs ';') -> consumer parses to
  one mega-column -> the mapping's source keys don't exist -> whole-chunk failure with a
  misleading mapping reason. A late silent mis-parse, the worst diagnostic outcome.

## Decisions locked

- Approach B (one source of truth): the WORKER detects the delimiter (it already sniffs it),
  publishes it on the envelope, and the CONSUMER reads CSV with that delimiter. Aligns with
  D54 (the event/envelope is the trust boundary; the consumer does not re-detect or guess).
  Not consumer-only auto-sniff (B avoids a third independent detector / relocated split-brain),
  not declared-on-upload (the uploader should not have to know the delimiter), not
  declared-on-template (the worker runs preflight mapping-blind, before any template loads).
- Detected set: comma, semicolon, tab, pipe. Whatever sniff_csv returns is carried; these
  four are the supported/expected delimiters.
- Quoted fields: handled by the parser's standard quote behaviour, NOT a new contract field.
  Polars read_csv respects '"'-quoted fields by default (a delimiter inside quotes is data,
  not a separator). 16f VERIFIES this holds when reading with the detected separator; it
  adds a quote-char field ONLY if a real file needs a non-'"' quote (out of scope unless
  verification fails).
- Envelope carries the delimiter only (minimal contract change). Not the full dialect
  (quote/escape/header) -- add later only if a file needs it.
- Low-confidence sniff: if the worker's detection is low-confidence/ambiguous, default to
  comma AND flag (the consumer reading with the wrong delimiter now fails loudly at the
  mapping gate, so a wrong guess surfaces, not silently corrupts). Confirm the sniff confidence
  signal exists in plan; if sniff_csv gives no confidence, just carry the detected delimiter.

## The change

1. Worker (csv-ingest-worker): in preflight, capture the delimiter sniff_csv already detects
   (add Delimiter to the SELECT). Carry it through to the published csv.received envelope.
2. Envelope csv.received: add a delimiter field (the detected separator). Update the schema
   (contracts/pubsub/csv.received.schema.json) and the publisher model.
3. Envelope ingress.ready: carry the delimiter forward verbatim (the worker already forwards
   other fields from csv.received). Update its schema + model.
4. Consumer (streaming-consumer): read the delimiter off the IngressReadyEvent and pass it as
   the separator to pl.read_csv (separator=<delimiter>) instead of the hardcoded comma.
5. Verify quoted-field handling: a '"'-quoted field containing the detected delimiter parses
   as one field (not split). Confirm Polars default quoting covers this with the detected
   separator.

## Scope

In: the worker preflight delimiter capture + publish; the two envelope schema/model
additions (delimiter field); the consumer read using the delimiter; the quoted-field
verification.

Out (with where each lands):
- Full-dialect carry (quote char, escape, header-row override) -- not now; add only if a
  file needs it.
- Encoding detection/normalization -- separate (a known rules-register item).
- Any mapping_rules / template change -- delimiter is a file property, stays out of the
  per-column model.
- Any services/dis-ui edit.

## Acceptance criteria

- A semicolon-delimited file (the Acqua_Sapone sample) ingests end to end: worker detects
  ';', the envelope carries ';', the consumer parses with ';' into the correct columns
  (not one mega-column).
- A tab-delimited and a pipe-delimited file likewise parse correctly (the detected
  delimiter flows through). A comma file still works (no regression).
- A '"'-quoted field containing the delimiter (e.g. a value with an embedded ';') parses
  as a single field, not split.
- Both envelope schemas (csv.received, ingress.ready) carry the delimiter; the consumer
  uses it; no hardcoded comma remains on the consumer read path.
- The fresh==migrated / contract-consistency: both envelope schemas and their models agree
  on the new field; the schema JSON and the Pydantic model match.
- make check / lint / mypy clean; tests in the same commit.

## Test surface

- Worker: preflight on a ';' (and tab, pipe) sample -> the published envelope carries the
  correct delimiter. Comma -> ',' (no regression).
- Consumer: given an ingress.ready with delimiter ';', read a ';' CSV into the right
  columns; with ',', a ',' CSV. A quoted field with an embedded delimiter -> one field.
- Envelope round-trip: csv.received -> ingress.ready preserves the delimiter.
- End-to-end (live stack, if feasible in the test harness): the ';' sample through the
  worker+consumer lands correctly (overlaps 16d; the formal pin can be a parse-level test).
- Negative/edge: low-confidence sniff -> default comma + flag (per the locked decision),
  if the confidence signal exists.

## Open questions for plan mode

1. Does sniff_csv expose a confidence/quote signal we should also capture, or only the
   delimiter? Confirm; lean delimiter-only.
2. The two envelopes are frozen contracts -- confirm the additive delimiter field is a
   backward-compatible addition (optional with a comma default for any in-flight message
   lacking it) so a mid-flight or replayed old envelope does not break. Lean: optional field
   defaulting to ',' for safety, the worker always sets it going forward.
3. Verify Polars read_csv with an explicit separator still respects '"' quoting for embedded
   delimiters (the quoted-field requirement) -- confirm in plan with the actual Polars version.
4. Where exactly the consumer read is (fetch.py:143) and whether any other read path
   (re-fetch, retry) also hardcodes comma and must be updated too.
