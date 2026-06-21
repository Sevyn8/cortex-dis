# `services/receiver-reverse-api/` — *deferred (not in v1.0)*

The puller for external APIs that Ithina reads from on a schedule. Inverted ingress: Ithina makes outbound calls and treats the response body as ingress. Identity is bound to the endpoint config registered for each pull target.

**Purpose.** Periodically pull data from external APIs that the platform integrates with, on behalf of tenants who have registered those endpoints, and convert each successful pull into a standard ingress event.

**Entry.**
- Trigger: scheduler tick at a configured cadence per (tenant, endpoint); long-running container with internal scheduler.
- Inputs: registered endpoint configs (per-tenant, per-source); persisted cursor state (last successful page or timestamp per endpoint).
- Preconditions: endpoint config active; credentials for the external API valid; cursor state available (initialize on first run).

**Process.**
- Scheduler determines which (tenant, endpoint) pairs are due to pull.
- For each due target: load cursor state; construct HTTP request (auth headers, pagination params); call external API.
- Paginate through results using the configured strategy (cursor, offset, page).
- For each page: resolve identity via §3.5 identity-service `resolve_from_endpoint` method; generate `trace_id`; tokenize PII per `dis-pii`; persist response body to GCS via `dis-storage`; write bronze metadata row; publish `ingress.ready`.
- Persist new cursor state on success of all pages.
- Emit audit events for each page and one summary event per pull.

**Exit.**
- Success: cursor state advanced; N pages each producing one GCS object + one bronze row + one `ingress.ready` message (consumed by §3.7 streaming-consumer). No HTTP response (this service does not accept inbound requests).
- Failure modes handled: external API 4xx (mark endpoint unhealthy, alert, skip until configured retry window); 5xx or timeout (retry with backoff up to N attempts); auth failure (mark credential expired, alert ops, do not advance cursor); partial pagination failure (advance cursor only to the last fully-processed page).
- Failure modes propagated: pipeline-side validation, mapping, or canonical write failures arrive on `ingress.ready` and are not the puller's concern.
- Edge case: cursor drift between Ithina and external API (external API resets pagination). The endpoint config supports a manual cursor reset via `tools/replay/`.


```
services/receiver-reverse-api/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
│
├── src/
│   └── receiver_reverse_api/
│       ├── __init__.py
│       ├── main.py             # scheduler entrypoint, not HTTP server
│       ├── config.py
│       │
│       ├── puller/             # per-pull-target logic
│       │   ├── __init__.py
│       │   ├── scheduler.py    # which targets to pull, when
│       │   ├── http_puller.py  # generic HTTP GET + auth
│       │   └── paginator.py    # cursor/offset/page pagination strategies
│       │
│       ├── enrichment/
│       │   ├── __init__.py
│       │   ├── identity.py     # call Identity Service resolve_from_endpoint
│       │   ├── trace.py
│       │   └── pii.py
│       │
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── gcs.py
│       │   ├── bronze.py
│       │   └── pubsub.py
│       │
│       ├── state/              # pull state per target (last cursor, last ts)
│       │   ├── __init__.py
│       │   └── cursor_store.py
│       │
│       └── clients/
│           ├── __init__.py
│           └── identity.py
│
├── tests/
│   ├── unit/
│   │   ├── test_paginator.py
│   │   ├── test_cursor_store.py
│   │   └── test_scheduler.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_pull_happy.py
│   │   ├── test_pull_paginated.py
│   │   └── test_pull_resume.py
│   └── fixtures/
│       └── responses/          # mock external API responses
│
├── scripts/
│   ├── run-local.sh
│   └── pull-once.sh            # one-shot manual trigger of a pull
│
└── deploy/
    ├── service.yaml
    ├── configmap.yaml
    └── README.md
```

**Why this service is shaped differently from the other receivers.** The other three receivers are HTTP servers (accept inbound). This one is a scheduled puller (make outbound calls). Different control flow: no request handler, no per-request auth from the caller. Instead there's a scheduler that decides what to pull when, and a state store that remembers where each pull left off.

**Why `state/` exists.** Reverse-API pulls are stateful: "give me everything since cursor X" or "give me page N+1." Losing state means re-pulling everything from the start, which is expensive and can violate rate limits at the source. Persisting cursor state per (tenant, endpoint) is essential.

**Why `puller/scheduler.py` is here and not in `tools/`.** The scheduler is part of the service's runtime, not a developer tool. It runs as part of the deployed container. `tools/` is for human-invoked utilities.

**What's deliberately not here.** No HTTP server (no `handlers/`). No webhook handling. No machine-auth-against-Ithina logic (this service is the one making outbound calls, not receiving them). The auth concern here is "how do we authenticate *to* the external API," which is per-endpoint config.

---
