###############################################################################
# project-services: enable the GCP APIs DIS needs.
###############################################################################

variable "project_id" {
  type = string
}

locals {
  services = [
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "run.googleapis.com",
    "pubsub.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "vpcaccess.googleapis.com", # Serverless VPC Access connector for Cloud Run -> private Cloud SQL
  ]
}

resource "google_project_service" "this" {
  for_each = toset(local.services)

  project = var.project_id
  service = each.value

  disable_on_destroy = false
}

output "enabled" {
  value = [for s in google_project_service.this : s.service]
}
