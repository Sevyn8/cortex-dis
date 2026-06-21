#!/usr/bin/env bash
# Bootstrap the Terraform state bucket ONCE, by hand.
#
# Why by hand: the GCS bucket that stores Terraform state cannot be managed by
# the same Terraform that uses it as a backend (chicken-and-egg). So we create
# it directly, then point the staging backend at it.
#
# Safe to run once. Re-running is harmless (mb fails if it exists; the rest is idempotent).

set -euo pipefail

PROJECT_ID="ithina-retail-dis"
STATE_BUCKET="sevyn8-tfstate"
REGION="asia-south1"

echo "Setting project to ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}"

echo "Creating state bucket gs://${STATE_BUCKET} in ${REGION} (uniform access)"
# -b on  : uniform bucket-level access (satisfies the uniform-access org policy)
# --pap  : enforced public access prevention
gcloud storage buckets create "gs://${STATE_BUCKET}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --uniform-bucket-level-access \
  --public-access-prevention || echo "Bucket may already exist; continuing."

echo "Enabling object versioning (so state history is retained)"
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning

echo "Done. The staging backend uses bucket=${STATE_BUCKET}, prefix=dis/staging."
echo "Next: cd ../envs/staging && terraform init"
