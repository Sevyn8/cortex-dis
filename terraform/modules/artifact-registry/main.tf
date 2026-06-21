###############################################################################
# artifact-registry: a Docker repository for DIS service images.
###############################################################################

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "repo_id" {
  type = string
}

resource "google_artifact_registry_repository" "this" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repo_id
  format        = "DOCKER"
  description   = "DIS service container images (staging)."
}

output "repo_url" {
  description = "Base URL for image pushes, e.g. asia-south1-docker.pkg.dev/PROJECT/REPO."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.this.repository_id}"
}
