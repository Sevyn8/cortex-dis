# DIS staging infrastructure (Terraform)

Starting scaffold for the DIS staging environment on GCP. This is a draft to refine and validate together with Sanjeev, not validated infrastructure. It has not been run through terraform init/validate/plan. Assumptions are marked inline; genuinely unknown values live in variables.

## Locked configuration

- GCP project: `ithina-retail-dis` (project number 1002062049747, org 395217984150)
- This project is treated as the STAGING environment. Production will be a separate project, a clean copy of envs/staging with its own tfvars.
- Region: asia-south1 (Mumbai). SFTPGo VM zone: asia-south1-a.
- Frontend (dis-ui): served from Cloud Run (a static-server container), to keep the stack uniform and Terraform managed. Production may revisit GCS + Cloud CDN.
- Sizing: smallest viable everywhere. Cloud SQL smallest tier with private IP; Cloud Run min instances 0 (scale to zero); SFTPGo e2-small; single zone; no HA.
- State backend: GCS bucket `sevyn8-tfstate` in project `ithina-retail-dis`, prefix `dis/staging`.

## Safe org-policy defaults baked in

These satisfy the strictest common org constraints, so a policy check should require minimal change:
- Cloud SQL has private IP only (no public IP), reached over private service access.
- No external IPs on VMs except the SFTPGo regional load balancer front end.
- Uniform bucket-level access on all buckets.
- Least-privilege service accounts per service.

## Org-policy reality (checked) and the access pattern

The org-policy check on org 395217984150 is done. Two defaults are enforced and shape the access design:

- `iam.allowedPolicyMemberDomains` = customer-id-only by default, so public Cloud Run (an allUsers run.invoker grant) is blocked.
- `compute.restrictProtocolForwardingCreationForTypes` = INTERNAL-only by default, so external load balancer forwarding is blocked.

Decision: copy the ithina-retail-admin pattern (public Cloud Run + app-level JWT and CORS auth, enabled via a project-level exemption). The IAP/LB/domain idea is dropped.

Two project-level exemptions are required on `ithina-retail-dis` before a full apply:

1. `iam.allowedPolicyMemberDomains` -> allowAll (for public Cloud Run).
2. `compute.restrictProtocolForwardingCreationForTypes` -> allow EXTERNAL (for the external SFTP LB).

Until (1) is set, keep `allow_public = false`. Until (2) is set, the external SFTP forwarding rule is rejected.

Access control is app-level: the frontend calls dis-ui-server over its public URL, and dis-ui-server enforces JWT (`AUTH_CLIENT_MODE=STUB` until real OIDC, D25) plus CORS scoped to the frontend URL.

For the hosted frontend to actually CALL dis-ui-server (rather than run its built-in fixtures), the dis-ui IMAGE must be BUILT in real mode. The UI reads two Vite vars, `VITE_DIS_UI_SERVER_MODE` and `VITE_DIS_UI_SERVER_BASE_URL`, which are BUILD-TIME only (baked into the static bundle by `vite build`); a Cloud Run runtime env does nothing for an already-built SPA. So build the image with the cloudbuild substitutions `_DIS_UI_SERVER_MODE=real` and `_DIS_UI_SERVER_BASE_URL=<dis-ui-server URL>` (= the deployed dis-ui-server URL, `var.dis_ui_server_url`). The default build is `fixture` (safe); without `_DIS_UI_SERVER_MODE=real` the hosted app serves fixtures. The previous `VITE_API_BASE` build/runtime var was dead and has been removed (the UI never read it).

NOTE: while auth is STUB, public staging is effectively open. Do not place sensitive data there until real OIDC lands.

## The schema seam (do not change)

Terraform creates the Cloud SQL instance and database ONLY. The schema is owned by Alembic (alembic/versions/0001 to 0005) and applied separately, after the instance exists and before the services that depend on it start. Terraform does not manage schema.

## Apply order

1. Bootstrap the state bucket once, by hand (chicken-and-egg: the bucket holding state cannot be managed by the Terraform that uses it). See bootstrap/bootstrap-state-bucket.sh.
2. From envs/staging: `terraform init` then `terraform plan`. Review the plan carefully (this is greenfield, every resource is a create).
3. Apply the durable layer first (project-services, network, service-accounts, cloud-sql, buckets, pubsub, secrets, artifact-registry). You can target these with -target during early applies if you want to bring them up before the service layer.
4. Set the Secret Manager secret VALUES out of band (gcloud or console). Terraform creates the secret containers only; values never go in HCL or state.
5. Run Alembic migrations against the new Cloud SQL instance. Confirm RLS is on.
6. Build and push images to Artifact Registry (dis-ui-server has a Dockerfile; csv-ingest-worker, streaming-consumer, mirror-sync-consumer need Dockerfiles written first). For the dis-ui frontend, pass the real-mode build-time vars so the hosted app calls the backend: `_DIS_UI_SERVER_MODE=real` and `_DIS_UI_SERVER_BASE_URL=<dis-ui-server URL>` (known after dis-ui-server deploys, so this is the second build). Omitting them builds a safe fixture-mode bundle.
7. Apply the service layer (the cloud-run-service instances and the sftpgo-vm).

## Layout

```
terraform/
  bootstrap/                         one-time state bucket creation
  envs/
    staging/                         the staging root: wires all modules
  modules/
    project-services/                enable required GCP APIs
    network/                         VPC, subnet, private service access, Cloud SQL private IP
    service-accounts/                one least-privilege SA per service + IAM
    cloud-sql/                       Postgres instance + ithina_dis_db (schema via Alembic)
    buckets/                         bronze, onboarding-staging, upload
    pubsub/                          topics + subscriptions (csv.received, ingress.ready, ...)
    secrets/                         Secret Manager containers (values set out of band)
    artifact-registry/               Docker repo for service images
    cloud-run-service/               reusable module for any Cloud Run service
    sftpgo-vm/                       GCE e2-small + regional TCP load balancer
```

## What is still TODO before a real apply

- Confirm the smallest Cloud SQL tier for your Postgres version (the legacy db-f1-micro / db-g1-small shared-core tiers are deprecated for newer Postgres; the smallest modern tier may be db-custom-1-3840). Set var.cloud_sql_tier accordingly.
- Write Dockerfiles for csv-ingest-worker, streaming-consumer, mirror-sync-consumer.
- Decide the image build/push path (Cloud Build vs Makefile/CI) and how images are referenced (by tag or digest). Today only the durable infra is fully scaffolded; the service deploys reference image variables you set after pushing.
- Pin the exact Pub/Sub topic/subscription set against contracts/pubsub in the app repo.
- Confirm the Customer Master / Identity Service connectivity from this project (the mirror reader role and network path).
