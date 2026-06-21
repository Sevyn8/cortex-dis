# `services/daily-compute/` — *v1.0*

The scheduled job that computes derived attributes (`velocity_7day`, `stock_age_days`, `unit_cost_trend_30day`, future signals) once per day per SKU. Runs Postgres-local; incremental (yesterday's signal_history row + today's events); writes new signal_history rows and updates `store_sku_current_position`. See `decisions.md` D31 and §4.20.

**Purpose.** Keep derived attributes on `store_sku_current_position` fresh daily for ROOS reads; build the daily history in `store_sku_signal_history` for backtesting and future incremental compute. Each run completes one day; the next run depends on this one's output.

**Entry.**
- Trigger: cron schedule via Cloud Scheduler, configured to retail off-hours, sequenced BEFORE §3.9 nightly-batch.
- Inputs: target date (defaults to yesterday); list of tenants to process (or all); compute config (signals to refresh, semantic versions).
- Preconditions: Cloud SQL reachable; yesterday's `store_sku_signal_history` rows exist (or bootstrap path needed); today's event partitions exist with completed data.

**Process.**
- For each tenant: set `app.tenant_id` via SET LOCAL.
- For each SKU in the tenant's `store_sku_current_position`:
  - Read yesterday's signal row from `store_sku_signal_history` (or fall back to BQ canonical_history if missing → slow path).
  - Read today's events from `store_sku_sale_events` and `store_sku_change_events` filtered by SKU + date.
  - Compute today's signal values (velocity, stock age, cost trend, etc.).
  - In one transaction: INSERT into `store_sku_signal_history` AND UPDATE the matching row in `store_sku_current_position`.
- Emit audit events per tenant + per SKU compute pass.

**Exit.**
- Success: today's signal_history rows inserted for every SKU; corresponding current_position rows updated with new signal values; job state advanced.
- Failure modes handled: missing yesterday row → slow-path BQ read; transient Cloud SQL error → retry with backoff; per-SKU compute error → log and continue (other SKUs not blocked).
- Failure modes propagated: persistent Cloud SQL outage → job exits non-zero; alert ops; tomorrow's run uses slow path for affected SKUs.

**Idempotency.** Re-running for the same date is safe: UNIQUE constraint on `(tenant_id, store_id, sku_id, sku_variant, sku_lot_batch, as_of_date)` prevents duplicate signal_history rows; UPSERT on current_position is naturally idempotent.

```
services/daily-compute/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── daily_compute/
│       ├── __init__.py
│       ├── main.py             # job entrypoint (one run = one full cycle for one date)
│       ├── config.py
│       │
│       ├── compute/            # per-signal compute logic
│       │   ├── __init__.py
│       │   ├── velocity.py     # velocity_7day
│       │   ├── stock_age.py    # stock_age_days
│       │   └── cost_trend.py   # unit_cost_trend_30day
│       │
│       ├── readers/
│       │   ├── __init__.py
│       │   ├── yesterday_signals.py  # read store_sku_signal_history
│       │   ├── today_events.py       # read sale + change events
│       │   └── bq_fallback.py        # slow path for missing yesterday
│       │
│       ├── writers/
│       │   ├── __init__.py
│       │   ├── signal_history.py     # INSERT into store_sku_signal_history
│       │   └── current_position.py   # UPDATE store_sku_current_position
│       │
│       ├── orchestration/
│       │   ├── __init__.py
│       │   ├── per_tenant.py         # iterate tenants with RLS context
│       │   └── per_sku.py            # iterate SKUs within a tenant
│       │
│       ├── idempotency/
│       │   ├── __init__.py
│       │   └── job_state.py          # date-watermark persistence
│       │
│       └── audit.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── scripts/
└── deploy/
```

**Why a separate service from nightly-batch.** Different purpose (compute vs export), different read patterns (per-SKU random reads vs bulk partition export), different write patterns (per-tenant UPSERT to hot table vs bulk INSERT to BQ). Splitting them keeps each focused; the scheduler orchestrates sequence.

---
