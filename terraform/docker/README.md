# DIS service images

Dockerfiles for the DIS services plus a Cloud Build pipeline. These are modeled
on the established pattern (the existing services/dis-ui-server/Dockerfile and
the repo's uv / pnpm tooling). CONFIRM the per-service entrypoints against each
service's real module before relying on them.

## The services

| Image | Dockerfile | Kind | Build context |
|---|---|---|---|
| dis-ui-server | services/dis-ui-server/Dockerfile (existing) | HTTP (BFF) | repo root |
| csv-ingest-worker | terraform/docker/csv-ingest-worker.Dockerfile | Pub/Sub consumer | repo root |
| streaming-consumer | terraform/docker/streaming-consumer.Dockerfile | Pub/Sub consumer | repo root |
| mirror-sync-consumer | terraform/docker/mirror-sync-consumer.Dockerfile | consumer | repo root |
| dis-ui | terraform/docker/dis-ui.Dockerfile | static SPA (nginx) | services/dis-ui |

The three workers build from the REPO ROOT because the Python workspace uses
shared libs/ and a root uv.lock. The frontend builds from services/dis-ui (its
own package root with pnpm-lock.yaml).

## What to confirm

- Entrypoints: each worker CMD assumes `python -m <pkg>.main`. Check the real
  module (some services may have a console script or a runner instead).
- Python version: assumed 3.12. Confirm against the root pyproject.toml.
- uv workspace package names: the `--package NAME` flags assume the package
  names match the service dirs. Confirm against pyproject.toml.
- The frontend mode + API base are BUILD-time Vite vars (baked into the bundle),
  so they must be passed as build args, not runtime env: VITE_DIS_UI_SERVER_MODE
  ('fixture' default, 'real' for staging) and VITE_DIS_UI_SERVER_BASE_URL (the
  dis-ui-server URL). The old VITE_API_BASE was dead (the UI never read it).

## Build all images (Cloud Build)

From the repo root:

```
gcloud builds submit --config terraform/docker/cloudbuild.yaml \
  --substitutions=_REGION=asia-south1,_REPO=dis-images,_DIS_UI_SERVER_MODE=real,_DIS_UI_SERVER_BASE_URL=https://dis-ui-server-XXXX.run.app .
```

This builds and pushes all five images tagged with the commit short SHA and
:latest. Then set the image_* variables in the Terraform tfvars to the pushed
URIs (by SHA for reproducibility) and apply the service layer.

For dis-ui specifically, prefer the dedicated terraform/docker/cloudbuild-dis-ui.yaml
(context services/dis-ui, all four VITE args, --no-cache) so the SPA can never ship a
stale cached bundle:

```
gcloud builds submit --config terraform/docker/cloudbuild-dis-ui.yaml \
  --substitutions=SHORT_SHA=<sha>,_STUB_TOKEN_TENANT=<tok>,_STUB_TOKEN_OPS=<tok> .
```

## Build a single image locally

```
# a worker (from repo root)
docker build -f terraform/docker/csv-ingest-worker.Dockerfile -t LOCAL/csv-ingest-worker .

# the frontend (from repo root, context is the frontend dir). Omit the args for a
# safe fixture-mode bundle; pass both for a real-mode bundle that calls dis-ui-server.
# --no-cache guarantees a fresh recompile (the context MUST be services/dis-ui; a
# repo-root context silently bakes a stale cached bundle).
docker build -f terraform/docker/dis-ui.Dockerfile --no-cache \
  --build-arg VITE_DIS_UI_SERVER_MODE=real \
  --build-arg VITE_DIS_UI_SERVER_BASE_URL=URL -t LOCAL/dis-ui services/dis-ui
```
