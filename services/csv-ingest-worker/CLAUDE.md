# services/csv-ingest-worker — Claude Code Context

Loaded when Claude Code works in `services/csv-ingest-worker/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

The CSV upload Phase-2 worker (D36): consumes the `csv.received` event dis-ui-server
publishes after a tenant's upload is confirmed saved in GCS (D54 — the trigger is the
event, NOT a GCS object-finalize). Runs DuckDB structural preflight, the dis-pii
fail-loud gate, the metadata-only bronze write, the `ingress.ready` publish, and
per-stage audit.

For the EPE block, file structure, and behavioural detail, see `README.md`. For the
current build slice, see `docs/slices/`.

**Status:** v1.0 (built in Slice 9b).

## Rules specific to this service

- **The event is the trust boundary (D54).** Identity (`tenant_id`/`store_id` UUIDs) and
  `trace_id` are READ off `csv.received` and trusted. This worker calls NO Identity
  Service, holds no identity-client dependency (`dis_core.identity` is never imported —
  a test enforces it), performs no external-to-internal translation, and NEVER mints a
  `trace_id` (hard rule 4; a test makes the mint explode to prove it). Identity freshness
  is the streaming consumer's validate (Slice 10), not this worker's.
- The GCS path is CROSS-CHECKED against the event (`split_object_uri` +
  `parse_object_path`, dis-storage only, hard rule 9); a mismatch is a malformed
  producer (`EventPathMismatchError`), never a re-resolution.
- Writes to: `bronze.data_ingress_events` (via `dis-rls` ONLY, hard rules 1 & 12) and
  Pub/Sub `ingress.ready`. Audit via `dis-audit` to `audit.events`. Nothing else.
  Statuses written: `RECEIVED`, `FAILED`; `PUBLISHED` via the publish mark.
- **Write-then-CONDITIONALLY-publish (D5 + OQ4):** bronze lands first; `ingress.ready`
  is published only on preflight success. Preflight failure → `FAILED` bronze row +
  FAILURE audit + ack, NO publish, no quarantine (quarantine is Slice 10/11).
- **Idempotency (D59):** key = `(tenant, upload_session_id, payload_sha256)`, 24h window
  measured against the prior row's `received_at`. PUBLISHED/FAILED prior → full no-op
  returning the prior `trace_id`. Unpublished RECEIVED prior → resume-and-mark: complete
  the lost publish under the PRIOR trace, stamp `published_at`, no second row. The key
  components are required values — missing ones raise (`EventContractError`); the check
  errors, never skips, when its backing store is absent. Query-based dedup is correct
  for a SINGLE worker instance only (D58) — do not scale instances without a
  constraint/upsert design.
- **PII gate before any persistence (hard rule 2, D40):** sniffed header names go
  through `dis-pii` via the synthetic `{"rename": {h: h}}` shape (wired, NOT extended).
  No backend exists in v1.0 → detection always raises. No config flag may disable the
  gate; the not-raise branch exists only via an injected backend in tests.
- **DuckDB is contained in `preflight.py`** (the only module importing it), with canary
  tests pinning the relied-on sniff behaviours (Slice 5 pattern). Preflight is
  structural ONLY; no column- or mapping-aware checks (Slice 10). DuckDB error text is
  never propagated (it can quote cell values) — stable reason codes only.
- **Audit is fire-and-forget (hard rule 11):** failures logged (alert-worthy, D45),
  never raised, never blocking; duplicates tolerated (D44); every event carries
  `tenant_id` (D43), `trace_id`, and the bronze id where one exists. Stage vocabulary
  is dis-audit's CLOSED enum — preflight detail rides `event_data` under `RECEIVED`.
- **Subscription provisioning is `make topics-create`** (tools/local/create_topics.py),
  never worker runtime code: an absent subscription is a loud startup error, not an
  auto-repair. The runtime publisher/subscriber refuse to run without
  `PUBSUB_EMULATOR_HOST` (cloud wiring is deferred infra).
- **Two `received_ts` exist on this flow:** `csv.received.received_ts` (producer's) vs
  `ingress.ready.received_ts` (= bronze `received_at`, when DIS durably accepted).
  Never treat them as the same instant; the dedup clock is bronze `received_at`.
- Both envelopes are frozen contracts (hard rule 10): populate, never change shape;
  drift guards reconcile the models against the contract files both directions.
- Errors are `dis-core` `CsvIngestError`-family; logs bind `service`/`stage`/
  `tenant_id`/`trace_id`; never log PII or payloads.

## When uncertain

Refer to the current slice doc in `docs/slices/`. If silent there, refer to
architecture. If still uncertain, ask before coding.
