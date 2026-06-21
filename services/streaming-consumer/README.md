# `services/streaming-consumer/` — *v1.0 target; Slice 10 (happy path) + Slice 11a (quarantine direct-write) built*

The ELT pipeline. Reads `ingress.ready`, fetches the chunk from bronze + GCS, looks up the active mapping, validates identity, runs pre-mapping validation, applies the mapping (rename → normalize → cast → derive), runs post-mapping validation, and atomically writes the canonical row (hot upsert + event insert in one Cloud SQL transaction) or routes failures to quarantine. The largest service in DIS by code volume. For a directory-by-directory walkthrough see `build-guide.md`.

> **Build state (Slice 10 + 11a).** This README describes the v1.0 TARGET. Built now:
> the happy path — intake, bronze/GCS fetch, per-lookup mapping load + routing, both
> Pandera gates, the four sub-stages, `mapping_version_id` stamping, the atomic
> dual-write (D30/D63/D64), read-time-dedup posture (D33/D38/D65), per-stage
> fire-and-forget audit (D42) — and the Slice 11a storm stopper: a narrow allowlist
> of known-deterministic failures is written DIRECTLY to `quarantine.*` (via
> `libs/dis-quarantine`, fail-loud), the reserved `QUARANTINED` audit stage is
> emitted, and the message ACKS; everything else keeps audit-and-nack.
> NOT built yet: the `quarantine` topic publish + drainer (Slice 11b — the direct
> write is the 11a interim), replay/lifecycle transitions (only status=NEW is
> written), `ingress.resubmit`/replay (Slice 12), the Identity Service `validate()`
> + D28 fallback (Slice 13 — identity arrives resolved on the event; the composite
> FK is the enforcement, D39), the circuit breaker + `pipeline.dlq` (D27, carried),
> STAGED shadow reads, and BQ audit. The service `CLAUDE.md` carries the built
> invariants.

**Purpose.** Transform raw chunks into canonical rows with full audit, RLS enforcement, and tenant-scoped batching. The only service that writes to canonical schemas (other than daily-compute which writes signal_history).

**Entry.**
- Trigger: Pub/Sub messages on `ingress.ready` (fresh ingress) and `ingress.resubmit` (replays). Producers: §3.1-§3.4 receivers; quarantine resubmit flow.
- Inputs: ingress envelope `{trace_id, tenant_id, store_id, source_id, gcs_uri, received_ts}`.
- Preconditions: bronze row exists; mapping for `(tenant_id, source_id)` has `status=ACTIVE` or `STAGED`; identity_mirror has tenant/store rows; Cloud SQL reachable.

**Process.**
- Pull message; ack-extend if processing approaches deadline.
- Fetch bronze metadata row (gcs_uri, payload hash, expected row count) and the chunk bytes from GCS via `libs/dis-storage`.
- Look up active mapping in `config.source_mappings`; cache hit refreshed by `mapping.changed`.
- Validate tenant/store via §3.5 identity-service `validate()`. Circuit-breaker fallback: direct `identity_mirror` read.
- Pre-mapping (source-shape) validation with Pandera suite referenced by the mapping. Failed rows → quarantine envelope on `quarantine` topic; chunk-level failures → `quarantined_chunks`.
- Apply mapping in four sub-stages (rename, normalize, cast, derive); produce canonical-shape rows in memory.
- Stamp `mapping_version_id` on every produced row (see `decisions.md` D22).
- Post-mapping (canonical-shape) validation. Failed rows → quarantine.
- Batch valid rows by `tenant_id`; open a Cloud SQL transaction per batch (manual batching ~500 rows); `SET LOCAL app.tenant_id`; atomic dual-write: upsert into `canonical.store_sku_current_position` AND insert into the matching event table (`store_sku_sale_events` or `store_sku_change_events`). Either both succeed or both roll back. See `decisions.md` D30.
- Emit INGRESS_EVENT-scoped audit events at each stage (RECEIVED, MAPPING_LOOKED_UP, IDENTITY_VALIDATED, PRE_MAPPING_VALIDATED, MAPPING_EXECUTED, POST_MAPPING_VALIDATED, CANONICAL_WRITTEN). Emit ROW-scoped audit events for any failed rows. See audit volume model (Option B) in BQ schema docs.
- Cloud SQL health check (circuit-breaker): `SELECT 1` with 100ms timeout before each batch. Unhealthy → route batch to `pipeline.dlq` topic; receivers receive backpressure signal.
- Ack message on successful commit.

**Exit.**
- Success: canonical rows written (hot + event); audit events emitted; message acked.
- Quarantine: invalid rows routed to `quarantine` topic; consumed by §3.8 quarantine-drainer. *(11a interim: the consumer writes `quarantine.*` directly and ACKS — the topic-mediated path supersedes the call site in 11b, not the `dis-quarantine` write functions.)*
- DLQ: Cloud SQL unhealthy → batch routed to `pipeline.dlq`; receivers throttle.
- Replay: `ingress.resubmit` triggers full reprocess; `mapping_version_id` defaults to the version on the row being replayed (not current ACTIVE).
- Failure propagation: persistent Cloud SQL outage → repeated nack until DLQ drainer takes over; ops alerted.

**Why this service is large.** Five concerns combined: bronze fetch, mapping execution, validation (pre + post), canonical sink (atomic dual-write), and audit emission. Splitting would scatter the transaction boundary; canonical writes need to be one transaction.

**What's deliberately not here.** No mapping authoring (that's dis-ui-server). No signal computation (daily-compute). No tenant identity management (identity-service). No quarantine LIFECYCLE (transitions/replay — the consumer only holds status=NEW via `libs/dis-quarantine`; the drainer takes over row writing in 11b).

---
