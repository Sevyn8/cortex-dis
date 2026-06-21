# `services/nightly-batch/` — *v1.0*

The scheduled job that runs during retail off-hours: exports yesterday's event-table and signal-history partitions from Cloud SQL to BigQuery from day 1, and drops Postgres partitions only after the retention window elapses (35 days default per `decisions.md` D29). At v1.0 beta scale (~150K events/day across 5 tenants × ~25 stores), Cloud SQL handles the 35-day buffer comfortably; the v0.8 24-hour window was based on 100M-events/day worst case.

**Purpose.** Copy yesterday's event tables and signal history from Cloud SQL into BigQuery for permanent archive and analytics; reclaim Cloud SQL space via partition drop after retention. Each nightly run is one full cycle of copy + verify + (optional) drop; the job is idempotent and resumable.

**Entry.**
- Trigger: cron schedule via Cloud Scheduler, configured to retail off-hours, sequenced AFTER §3.11 daily-compute completes.
- Inputs: implicit "yesterday" watermark from the previous successful run; list of partitioned tables to export (`canonical.store_sku_sale_events`, `canonical.store_sku_change_events`, `canonical.store_sku_signal_history`).
- Preconditions: Cloud SQL reachable; BigQuery dataset `canonical_history.*` exists; daily-compute has completed for the target date.

**Process.**
- Determine yesterday's watermark from `idempotency/job_state.py` (handles re-run after partial failure).
- For each table: export the day's partition from Cloud SQL → GCS (parquet) → BigQuery load into `canonical_history.*`.
- Verify: row count + checksum match between Cloud SQL partition and BigQuery load.
- On verified success: DROP the Cloud SQL partition (one DDL statement; instant).
- Update job state to mark this date as complete.
- Emit audit events for each step.

**Exit.**
- Success: BigQuery `canonical_history.*` populated for yesterday; Cloud SQL partitions dropped; job state advanced.
- Failure modes handled: BQ load failure → retry with backoff, then leave state at "loaded but not verified" (no drop); verification failure → alert ops, do not drop, leave state at "loaded, verify failed"; drop failure → alert ops, do not advance state (next run retries drop only).
- Failure modes propagated: persistent BigQuery or Cloud SQL outage → job exits non-zero; next scheduled run resumes from saved state.

**Note on retention window.** The 35-day buffer (configurable) gives ops a long replay surface in Cloud SQL. A missed daily run causes one extra day of events to accumulate, well within Postgres comfort. Alert thresholds fire after one missed run.


```
services/nightly-batch/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── nightly_batch/
│       ├── __init__.py
│       ├── main.py             # job entrypoint (one run = one full cycle)
│       ├── config.py
│       │
│       ├── steps/              # ordered batch steps
│       │   ├── __init__.py
│       │   ├── 01_watermark.py     # determine yesterday's slice
│       │   ├── 02_quality_gate.py  # optional Pandera suite on the slice
│       │   ├── 03_load_to_bq.py    # Storage Write API into canonical_history
│       │   ├── 04_verify_bq.py     # row count + checksum verify
│       │   └── 05_evict_sql.py     # delete > 3mo from Cloud SQL (batched)
│       │
│       ├── idempotency/        # safe re-runs
│       │   ├── __init__.py
│       │   └── job_state.py    # tracks which steps completed for a given run
│       │
│       └── clients/
│           ├── __init__.py
│           ├── bigquery.py
│           └── postgres.py
│
├── tests/
│   ├── unit/
│   │   ├── test_watermark.py
│   │   ├── test_quality_gate.py
│   │   └── test_evict_batching.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_full_cycle.py
│   │   ├── test_resume_after_failure.py
│   │   └── test_idempotent_rerun.py
│   └── fixtures/
│       └── history_slices/     # synthetic history data for testing
│
├── scripts/
│   ├── run-local.sh
│   └── run-once.sh             # manual one-shot trigger
│
└── deploy/
    ├── cronjob.yaml            # k8s CronJob or Cloud Scheduler trigger
    ├── configmap.yaml
    └── README.md
```

**Why `steps/` is numbered.** This is a batch job with a strict order. Numbered prefixes make the order visible from the directory listing and make Claude Code reliably write step N+1 after step N. Not used elsewhere; appropriate here because order is load-bearing.

**Why `idempotency/` exists.** The job has to be re-runnable safely. If step 03 fails after partial load, the next run needs to know what was already loaded. Job state tracking is the difference between safe re-run and corruption.

**Why `cronjob.yaml` instead of `service.yaml`.** This is a scheduled job, not a long-running service. Different deployment object.

**What's deliberately not here.** No streaming logic. No mapping. No quarantine handling. This is a periodic ETL job, narrow and bounded.

---
