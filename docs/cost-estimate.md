# DIS Cost Estimate

**Audience:** human operator. Rough monthly infrastructure cost at v1.0 beta scale, with growth-stage projections.
**Status:** projection at a point in time. Real bills depend on region, sustained-use and committed-use discounts, and actual traffic shape. Treat as ±30%.
**Method:** GCP list prices, US/EU regions (India region within ±10%).

**Companion docs.**
- `architecture.md` — system rationale.
- `decisions.md` — D4 (streaming runtime migration trigger), D29 (35-day Cloud SQL retention).
- `build-guide.md` — migration triggers consolidated.

---

## 1. Assumptions

v1.0 beta target:

- 5 tenants × ~25 stores × ~5000 SKUs.
- ~150K events/day aggregate ingress (~1.7 events/sec average, bursty).
- ~10-50 GB/month new canonical data (events + signal history).
- Cloud SQL retention: 35 days (configurable).
- Standard regional redundancy (HA where it exists, no multi-region).
- US/EU pricing.

## 2. Baseline (v1.0 beta scale)

| Component | Spec | Monthly (USD) |
|---|---|---|
| Streaming consumer (containerised service) | Scales to zero between bursts; ~1.7 events/sec average | $30-80 |
| Cloud SQL Postgres (canonical + bronze + config + identity_mirror + quarantine + staging) | db-custom-2-8GB, HA (regional), 100GB SSD | $250-350 |
| Cloud SQL read replica | Same size, single zone | $120-150 |
| Pub/Sub | <1B messages, <10GB throughput (`ingress.*`, `quarantine`, `pipeline.dlq`, `mapping.changed`, `identity.changed`) | $40-80 |
| BigQuery storage | ~50GB active + 100GB long-term (canonical_history + audit_events) | $5-15 |
| BigQuery streaming inserts + queries | Audit events + dis-ui-server analytics queries via BqClient | $20-50 |
| GCS | ~100GB standard + lifecycle to Nearline/Coldline (bronze + dlq + replay-staging) | $5-15 |
| Identity cache (Memorystore Redis Basic) | 1GB | $35-50 |
| Other containerised services (csv-ingest-worker + identity-service + mirror-sync-consumer + quarantine-drainer + nightly-batch + daily-compute + dis-ui-server) | ~7 small services in v1.0 | $80-180 |
| Cloud Scheduler | A few cron jobs (nightly batch, daily compute, identity tasks) | <$5 |
| Cloud Logging + Monitoring | Standard ingestion, default retention | $30-80 |
| Networking (egress, NAT, load balancers) | Modest internal traffic, some egress | $50-100 |
| Secret Manager, IAM, KMS, misc | Per-tenant PII keys in KMS | $5-15 |
| **Total (v1.0 baseline)** | | **~$680-1,170 / month** |

## 3. Where the money goes

- **~40%** Cloud SQL primary + HA + replica. Most expensive piece; load-bearing for canonical writes, RLS, FK enforcement, 35-day event buffer.
- **~15%** Other containerised services + identity cache.
- **~10%** BigQuery + GCS. Cheap at low volume; grows linearly with data retained.
- **~15%** Everything else (Pub/Sub, logging, networking).
- **<10%** Streaming consumer compute. Scales to zero between bursts at beta scale; this share inverts at scale (see §6).

## 4. Cost reduction options

Ranked from "free money" to "real compromise."

### 4.1 Free or near-free

- Containerised service scale-to-zero is already baseline; no further savings here at beta.
- **Sustained-use discounts:** automatic ~10-30% off compute, no action needed.
- **BigQuery long-term storage pricing:** ~50% off storage for data not modified in 90 days. Automatic.
- **GCS lifecycle rules** (Nearline after 30 days, Coldline after 1 year): ~70% off old blob storage. Configurable in Terraform.

### 4.2 Cheap with mild tradeoffs

- **Skip the Cloud SQL read replica** until product needs it: saves $120-150/month; product reads go to the primary in the meantime. Promote per `build-guide.md` §4 trigger.
- **Use in-process LRU cache in identity-service** instead of Memorystore Redis: saves $35-50/month; worse cache hit rate across replicas, acceptable at beta scale.
- **Tune Cloud Logging retention** and add exclusions for noisy logs: saves $20-50/month.

### 4.3 Real compromises

- **Drop Cloud SQL HA (single-zone):** saves $100-150/month; accept single-zone outage risk. Defensible at beta with a small tenant count; harder once SLAs apply.
- **Single-instance Cloud SQL (no HA, no replica):** saves $200-300/month; clearly beta-only.

## 5. Frugal beta scenarios

- **Frugal v1.0** (skip read replica, in-process identity cache instead of Memorystore): **~$500-600/month**.
- **Ultra-frugal v1.0** (frugal + drop Cloud SQL HA, single-zone): **~$380-470/month**, accepting single-zone outage risk.

## 6. Where cost grows fast

- **Streaming runtime inverts at scale.** When the streaming consumer hits the migration trigger (`decisions.md` D4: sustained 500+ rows/sec for 7 days), expect a step change. Dataflow always-on baseline ~$200-300/month + per-row processing cost.
- **Cloud SQL grows with storage and instance size.** At ~500GB + a larger instance class: $600-1,000/month. The 35-day retention window keeps Cloud SQL bounded; if retention is raised, costs scale linearly with the window.
- **BigQuery storage stays cheap; BigQuery query cost depends on analytics consumer behaviour.** A few badly-written dashboards can add $500-2,000/month. Set query quotas per project early.
- **Cloud Logging can quietly become expensive at scale.** Verbose audit logs at thousands of rows/sec can hit $500+/month. Tunable via exclusions and log-based metrics.
- **PII tokenization (KMS).** Per-tenant key operations are cheap individually but scale with event volume. At growth-stage rates, KMS could reach $50-150/month.

At growth-stage volume (1-5k rows/sec sustained), expect **~$3,000-6,000/month** with HA, replicas, BQ usage growing, higher-throughput runtime active. Sustained-use and committed-use discounts typically take real bills 30-50% below list at this scale.

## 7. Caveats

- These are **list prices**. Sustained-use discounts (automatic) shave ~10-30%. Committed-use discounts (1-3 year commitments) shave another 20-40% on compute.
- **Biggest unknown:** BigQuery query cost at the analytics consumer end. Worth setting query quotas per project and monitoring from day one.
- **Biggest hidden cost:** logging if left untuned. Worth setting log-based metrics and exclusions before turning on verbose audit emission.
- **Currency:** all figures USD. India billing converts at GCP's current FX; treat as ±5% drift.

## 8. Revisit cadence

This doc is a projection at a point in time. Re-estimate when:

- Tenant count crosses 10.
- Event volume crosses 1M/day or 500/sec sustained.
- Cloud SQL storage crosses 200GB.
- A migration trigger from `build-guide.md` §4 fires.
- Any GCP price change announcement is acted on.
