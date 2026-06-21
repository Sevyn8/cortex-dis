# Slice 40a: cloud-wiring (app side) — Pub/Sub real-GCP mode + readiness healthz workers

Unblock the first GCP deploy on the application side. Two app-level blockers from the terraform
audit stand between the services and a functioning Cloud Run deploy, both in our repo, neither
fixable by infra:

- A2: the Pub/Sub clients refuse to talk to real GCP by design, they raise at construction unless
  PUBSUB_EMULATOR_HOST is set ("cloud wiring is deferred infra"). In GCP there is no emulator, so
  every Pub/Sub publish and consume throws at startup. This slice adds the real-GCP branch.
- A5 (app half): the two pull-loop workers (csv-ingest-worker, streaming-consumer) are infinite
  Pub/Sub loops with no HTTP server, so they fail Cloud Run Service health checks. This slice adds
  a readiness /healthz HTTP server alongside the loop, built switch-ready so the eventual move to
  Cloud Run Worker Pools is a config change, not a code change.

This is the app-side slice. The infra-side fixes (env-var names, contract topic/subscription
names, Dockerfile/Cloud Build paths, secret values, the ui-server publisher role, the Pub/Sub DLQ
policy, the worker Cloud Run shapes) are Amit's terraform, tracked separately. mirror-sync is a
Cloud Run Job + Scheduler (infra); this slice does not touch its shape.

## The model

The whole slice is the emulator-or-ambient pattern applied where it is missing. The dis-storage
client already does this correctly: emulator when the emulator env var is set, real GCS via
ambient service-account credentials when it is not (libs/dis-storage/src/dis_storage/client.py).
A2 ports that same shape to every Pub/Sub client. A5 applies the same env-driven philosophy to the
worker's HTTP server: present when a toggle says so (Cloud Run Service mode), absent otherwise
(local, and future Worker Pools mode). One env-driven switch, multiple environments, no code
forks.

## Scope

### A2 — Pub/Sub real-GCP mode (FOUR clients)

In: add a real-GCP branch to every Pub/Sub client so that when PUBSUB_EMULATOR_HOST is set they
use the emulator (local, unchanged), and when it is absent they connect to real Google Pub/Sub
using the service account's ambient credentials and run normally.

There are FOUR emulator-guarded Pub/Sub constructors (the audit and the first draft of this doc
undercounted as "three"; CC found the fourth in grounding, the csv-ingest-worker both subscribes
AND publishes):
1. dis-ui-server publisher (publishes csv.received on upload) — publisher.py.
2. csv-ingest-worker subscriber (pulls csv.received).
3. csv-ingest-worker publisher (publishes ingress.ready; its own PubsubPublisher at
   publisher.py:108-117, constructed unconditionally at main.py:35). If this one keeps its guard,
   the worker still crashes on boot in GCP even after the others are fixed, and the slice goal is
   unreachable. It is in scope.
4. streaming-consumer subscriber (pulls ingress.ready).

All four get the emulator-or-ambient treatment. The existing emulator behaviour and the local
development path are unchanged for all four; this only adds the no-emulator branch that today
raises. Mirror the dis-storage emulator-or-ambient structure.

Confirm in grounding whether the two publishers share a PubsubPublisher class (so fixing the class
once covers both) and whether the two subscribers share a base, this decides whether A2 is a small
number of shared-class changes or four parallel edits. Either way all four constructors must end
up with the real-GCP branch.

### A5 — readiness healthz worker wrapper (the two pull-loop workers)

In: each pull-loop worker (csv-ingest-worker, streaming-consumer) gains a minimal HTTP server that
satisfies Cloud Run Service health checks while the real pull loop runs in the background. Built
switch-ready:
- The pull loop stays a standalone core that knows nothing about HTTP, callable on its own.
- The HTTP server is behind a runtime env-var toggle. Toggle on -> start the healthz server AND
  run the loop in the background (Cloud Run Service mode). Toggle off / unset -> run the pure loop
  directly (local dev, and the future Worker Pools mode). The later switch to Worker Pools is then
  a config flip (toggle off) with no app code change.
- The health check is READINESS, not liveness: the pull loop writes a heartbeat (a timestamp /
  liveness marker) each cycle; /healthz returns healthy only if the heartbeat is fresh, and
  unhealthy if it is stale. So a dead loop (crashed background task while the HTTP server lives on)
  reports unhealthy and Cloud Run restarts the worker, closing the zombie-worker failure mode that
  liveness-only would leave silent.
- The loop writes its heartbeat UNCONDITIONALLY (in all modes, including toggle-off); only the
  SERVER is toggled, so the loop code is byte-identical across local / Service / Worker Pools and
  never branches on environment.
- The HTTP server reads the port from the PORT env var (Cloud Run injects it) and serves the
  health path defined in the health-check contract (shared with Amit; see the contract doc). Return
  a healthy status on the agreed path when the heartbeat is fresh, unhealthy otherwise.

### Out

- The infra side (Amit's terraform): env-var names / composing POSTGRES_URL, contract
  topic/subscription names (A3/A4), the Dockerfile and Cloud Build path bugs (A7/A8), secret values
  (A6), the ui-server pubsub.publisher role (B4), the Pub/Sub DLQ policy (B9), the worker Cloud Run
  shapes (Service config: min-instances=1, CPU-always-allocated, max-instances cap; mirror-sync as
  a Job + Scheduler). Named so the boundary is clear; not built here.
- mirror-sync-consumer is a DB pull job from Customer Master with NO Pub/Sub usage (expected;
  confirm in grounding by grepping its package for any Pub/Sub client/import). It is a Cloud Run
  Job (run-to-completion, no loop, no healthz wrapper). This slice does not change it and does not
  add it to A2. If grounding unexpectedly finds it uses Pub/Sub, flag it, do not silently fold it
  in.
- No change to the pull loop's processing logic, the audit/quarantine behaviour, the dedup keys,
  or any pipeline semantics. This slice is transport-and-lifecycle wiring only.
- No services/dis-ui edit. No schema change. No new Pub/Sub topics or subscriptions (those are
  infra/contract).

## Depends on

- The dis-storage emulator-or-ambient client (the reference pattern for A2):
  libs/dis-storage/src/dis_storage/client.py.
- The four Pub/Sub clients at HEAD: the dis-ui-server publisher (publisher.py), the
  csv-ingest-worker subscriber, the csv-ingest-worker publisher (publisher.py:108-117, built at
  main.py:35), the streaming-consumer subscriber
  (streaming_consumer/clients/pubsub.py). The current emulator-required guards are the code this
  slice changes.
- The two worker entrypoints: csv_ingest_worker/main.py, streaming_consumer/main.py (where the
  toggle + healthz wrapper go, around the existing loop).
- The terraform audit (the A2/A5 findings and the file/line evidence).
- The health-check contract (port = PORT, path, readiness) documented and shared with Amit so the
  Cloud Run Service config matches the worker's server.
- Decision to REGISTER (operator assigns at the gate): the cloud-wiring posture, all Pub/Sub
  clients use emulator-or-ambient (real GCP via ambient credentials when no emulator), mirroring
  dis-storage; the pull-loop workers run a readiness-healthz HTTP server behind a runtime toggle so
  Cloud Run Service health checks pass and the later Worker Pools switch is config-only; the
  readiness (heartbeat) choice over liveness, and why (closes the zombie-worker silent stall).

## Goal

After this slice: with no emulator var set and ambient service-account credentials available, the
publishers publish and both subscribers consume against real Pub/Sub (all four clients); locally
(emulator var set) everything behaves exactly as before. The two pull-loop workers, with the
healthz toggle on, serve a readiness /healthz that passes Cloud Run Service health checks while the
loop runs, and a dead loop reports unhealthy. With the toggle off, the workers run the pure loop
(local, and future Worker Pools) with no code difference. The app side of the deploy is unblocked;
the services can boot and run on Cloud Run once Amit's infra-side fixes land.

## Task

Decompose in plan mode and show the design before code. Touches the four Pub/Sub clients, the two
worker entrypoints, and the worker config (the toggle + PORT). Confirm live shapes; do not assert
them.

Plan-mode grounding (ERROR, not skip):
- The dis-storage emulator-or-ambient client: the exact pattern (how it branches on the emulator
  var, how it builds the real client with ambient credentials) so A2 mirrors it faithfully.
- The four Pub/Sub clients' current construction guards (the emulator-required raise): what each
  needs to build a real-GCP client (project id, credentials, topic/subscription resolution), and
  whether the two publishers share a PubsubPublisher class and the two subscribers share a base, or
  they are separate implementations (decides whether A2 is a few shared-class changes or four
  parallel edits).
- The two worker entrypoints (main.py): the current loop structure, how the loop is launched, and
  where a toggled HTTP server + background-loop launch fits without changing the loop's logic.
- The config modules: where PUBSUB_EMULATOR_HOST, PUBSUB_PROJECT_ID, PORT, and the new healthz
  toggle are read; whether a shared config pattern exists.
- mirror-sync-consumer: confirm it has NO Pub/Sub client (DB pull job only); exclude it from A2.
- The async model: the workers are async pull loops; how to run an HTTP server (e.g. a minimal
  uvicorn/Starlette app, or the stdlib) concurrently with the loop in one process without one
  starving the other.

Design deliverables shown in the plan:
A. The A2 real-GCP branch for the four Pub/Sub clients (the emulator-or-ambient structure, the
   shared vs per-client shape), proving the local emulator path is unchanged.
B. The A5 worker wrapper: the env-var toggle, the standalone-loop / background-launch structure,
   the readiness /healthz (heartbeat write in the loop, freshness check in the route, the staleness
   threshold), the PORT read, the path per the contract. Show that toggle-off runs the pure loop
   (Worker-Pools-ready) and toggle-on runs server+loop.
C. The health-check contract (port, path, readiness semantics, staleness threshold) as the
   artifact shared with Amit.

Tests (same commit):
- A2: with the emulator var set, the four clients use the emulator (unchanged, existing tests
  green); with it unset, each constructs a real-GCP client without raising (the branch that today
  raises now succeeds) — proven without needing real GCP (mock/inspect the client construction,
  or the dis-storage test pattern for the ambient branch).
- A5 readiness: /healthz returns healthy when the heartbeat is fresh and unhealthy when it is
  stale (drive both); the loop writes the heartbeat each cycle; the server reads PORT.
- A5 toggle: toggle off -> the worker runs the pure loop, no HTTP server started (Worker-Pools /
  local shape); toggle on -> server + loop both run. Prove the loop logic is identical across both
  (the loop is the same callable).
- A5 concurrency: the HTTP server and the loop run together without the loop blocking the server
  (a long loop iteration does not make /healthz time out) — or document the chosen concurrency
  structure that prevents it.
- The loop's processing logic, audit, and quarantine behaviour are unchanged (existing worker
  tests green).

## What this slice does NOT do

No infra / terraform (Amit's: env vars, topic/subscription names, Dockerfiles, Cloud Build,
secrets, IAM, DLQ, Cloud Run shapes). No change to the pull loop's processing, audit, quarantine,
or pipeline semantics. No mirror-sync shape change (and mirror-sync is not an A2 client). No
services/dis-ui edit. No schema change. No new topics/subscriptions. The real-GCP path is added but
not exercised against real GCP in tests (that is the deploy itself).

## Open questions for plan mode

1. Do the two publishers share a PubsubPublisher class and the two subscribers share a base, or are
   the four clients separate implementations? (Decides whether A2 is a few shared-class changes or
   four parallel edits.)
2. The concurrency structure for server + loop in one process (async tasks vs thread): which
   prevents the loop starving the healthz, given the workers' async model.
3. The readiness staleness threshold (how long since the last heartbeat before /healthz reports
   unhealthy): a sensible default tied to the loop's expected cycle time; CC proposes.
4. Confirm mirror-sync-consumer has no Pub/Sub client (expected: DB pull job only) and is excluded.
5. The healthz toggle's env-var name and the health path: align with the contract doc shared with
   Amit (propose names; the contract is the shared source of truth).

## Acceptance criteria

- LOCAL CONFIGURATION UNCHANGED (the load-bearing guarantee): the same code/artifact runs locally
  and on GCP, with behaviour switched ONLY by env vars, never by code edits or file swaps. With the
  local configuration (PUBSUB_EMULATOR_HOST set, the healthz toggle off/unset), the publishers and
  both workers behave exactly as they do today, no new required env vars for local, no code
  difference, existing local/dev tests green. The pull loop logic is byte-identical across local /
  Cloud Run Service / Worker Pools (only the env-var switches differ); prove the toggle-off path
  runs the same loop callable, and that the heartbeat is written unconditionally (so the loop does
  not branch on environment).
- All four Pub/Sub clients use the emulator when PUBSUB_EMULATOR_HOST is set and a real-GCP client
  (ambient credentials) when it is not; the local emulator path is unchanged (existing tests green).
- Each pull-loop worker serves a readiness /healthz (reads PORT, healthy on fresh heartbeat,
  unhealthy on stale) when the toggle is on, and runs the pure loop when the toggle is off; the
  loop logic is identical across modes.
- The HTTP server and the loop run concurrently without the loop starving the healthz.
- The loop's processing, audit, and quarantine behaviour are unchanged.
- No infra/terraform, no mirror-sync shape change (and mirror-sync is not an A2 client), no dis-ui
  edit, no schema change.
- The health-check contract (port, path, readiness, threshold) is documented for Amit.
- The register entry (the cloud-wiring posture; emulator-or-ambient Pub/Sub across all four
  clients; the toggled readiness-healthz worker; the readiness-over-liveness choice; switch-ready
  for Worker Pools) is recorded.
- make check / lint / mypy clean; tests in the same commit.
