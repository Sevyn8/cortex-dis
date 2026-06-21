###############################################################################
# secrets: Secret Manager CONTAINERS only. Values are set out of band (gcloud
# or console), never in Terraform code. Terraform never sees the secret values.
#
# Exception, handled safely: the DIS DB app-user password. To avoid a hardcoded
# password we GENERATE one with random_password and store it as a secret
# version. It lives in Terraform state (which is in the private state bucket),
# not in code. Prefer Cloud SQL IAM auth in production to remove passwords
# entirely.
###############################################################################

variable "project_id" {
  type = string
}

variable "secret_names" {
  description = "Names of secret containers to create (no values)."
  type        = list(string)
}

# --- Empty containers (values set out of band) -----------------------------

resource "google_secret_manager_secret" "this" {
  for_each = toset(var.secret_names)

  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }
}

# --- Generated DB app-user password ----------------------------------------
# Only used to seed the Cloud SQL user without a hardcoded password.

resource "random_password" "db_app" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret_version" "db_app" {
  # Stored under the dis-db-app-password container if present in the set.
  count = contains(var.secret_names, "dis-db-app-password") ? 1 : 0

  secret      = google_secret_manager_secret.this["dis-db-app-password"].id
  secret_data = random_password.db_app.result
}

output "secret_ids" {
  value = { for k, s in google_secret_manager_secret.this : k => s.id }
}

output "db_app_password" {
  description = "Generated DB app-user password (sensitive; from state, not code)."
  value       = random_password.db_app.result
  sensitive   = true
}
