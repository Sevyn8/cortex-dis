###############################################################################
# service-accounts: one least-privilege service account per DIS service,
# plus the IAM role bindings each needs. Bindings here are project-level for
# simplicity in staging; tighten to per-resource bindings for production.
###############################################################################

variable "project_id" {
  type = string
}

variable "name_prefix" {
  type = string
}

locals {
  # service short-name -> the project roles it needs
  service_roles = {
    "ui-server" = [
      "roles/cloudsql.client",
      "roles/secretmanager.secretAccessor",
      "roles/storage.objectAdmin", # signed URL issuance for uploads
      "roles/pubsub.publisher",    # D83: publishes csv.received to real Pub/Sub now
    ]
    "csv-ingest-worker" = [
      "roles/cloudsql.client",
      "roles/secretmanager.secretAccessor",
      "roles/storage.objectAdmin",
      "roles/pubsub.publisher",
      "roles/pubsub.subscriber",
      "roles/pubsub.viewer", # D83: _require_subscription needs pubsub.subscriptions.get
    ]
    "streaming-consumer" = [
      "roles/cloudsql.client",
      "roles/secretmanager.secretAccessor",
      "roles/storage.objectViewer",
      "roles/pubsub.publisher",
      "roles/pubsub.subscriber",
      "roles/pubsub.viewer", # D83: _require_subscription needs pubsub.subscriptions.get
    ]
    "mirror-sync-consumer" = [
      "roles/cloudsql.client",
      "roles/secretmanager.secretAccessor",
      "roles/pubsub.subscriber",
    ]
    "ui-frontend" = [
      # static server; no GCP data access needed
    ]
    "sftpgo" = [
      "roles/storage.objectAdmin", # writes uploads to the upload bucket
    ]
  }

  # flatten to (service, role) pairs for binding
  bindings = flatten([
    for svc, roles in local.service_roles : [
      for role in roles : {
        svc  = svc
        role = role
      }
    ]
  ])
}

resource "google_service_account" "sa" {
  for_each = local.service_roles

  project      = var.project_id
  account_id   = "${var.name_prefix}-${each.key}"
  display_name = "DIS ${each.key} (staging)"
}

resource "google_project_iam_member" "binding" {
  for_each = {
    for b in local.bindings : "${b.svc}:${b.role}" => b
  }

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.sa[each.value.svc].email}"
}

output "emails" {
  value = { for k, sa in google_service_account.sa : k => sa.email }
}
