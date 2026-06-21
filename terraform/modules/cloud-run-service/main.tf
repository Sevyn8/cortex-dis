###############################################################################
# cloud-run-service: reusable module for any DIS Cloud Run (v2) service.
# Scales to zero (min instances 0). Attaches the VPC connector so the service
# can reach private Cloud SQL. Wires the Cloud SQL connection, env vars, and
# secret-backed env vars.
###############################################################################

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name" {
  description = "Service name (already prefixed by the caller)."
  type        = string
}

variable "image" {
  description = "Container image URI. If empty, the caller should not instantiate this module yet."
  type        = string
}

variable "service_account_email" {
  type = string
}

variable "vpc_connector_id" {
  type    = string
  default = ""
}

variable "cloudsql_connection_name" {
  description = "project:region:instance, mounted so the service can reach Cloud SQL."
  type        = string
  default     = ""
}

variable "env" {
  description = "Plain (non-secret) environment variables."
  type        = map(string)
  default     = {}
}

variable "secret_env" {
  description = "Secret-backed env vars: ENV_VAR_NAME -> secret_id (latest version is used)."
  type        = map(string)
  default     = {}
}

variable "allow_public" {
  description = "If true, grant roles/run.invoker to allUsers (public), matching the ithina-retail-admin pattern (public Cloud Run + app-level JWT/CORS auth). PREREQUISITE: the project needs an iam.allowedPolicyMemberDomains exemption (allowAll, set at the project level the same way ithina-retail-admin has it); without that exemption the allUsers grant is rejected by the org default. Leave false until the exemption is in place, then flip to true."
  type        = bool
  default     = false
}

variable "min_instances" {
  type    = number
  default = 0
}

variable "max_instances" {
  type    = number
  default = 2
}

variable "cpu_idle" {
  description = "Cloud Run CPU allocation. false = CPU always allocated (a background pull loop keeps running when no request is in flight). null (default) omits the resources block entirely, preserving the prior shape (request-throttled CPU) for request-driven services."
  type        = bool
  default     = null
}

resource "google_cloud_run_v2_service" "this" {
  project  = var.project_id
  location = var.region
  name     = var.name

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    dynamic "vpc_access" {
      for_each = var.vpc_connector_id == "" ? [] : [1]
      content {
        connector = var.vpc_connector_id
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }

    containers {
      image = var.image

      # CPU allocation. Emitted ONLY when cpu_idle is set, so request-driven
      # services (cpu_idle unset -> null) keep their prior shape with no diff.
      # The background pull-loop workers pass cpu_idle=false (CPU always on).
      dynamic "resources" {
        for_each = var.cpu_idle == null ? [] : [1]
        content {
          cpu_idle = var.cpu_idle
        }
      }

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }

    dynamic "volumes" {
      for_each = var.cloudsql_connection_name == "" ? [] : [1]
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.cloudsql_connection_name]
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count = var.allow_public ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value = google_cloud_run_v2_service.this.uri
}

output "name" {
  value = google_cloud_run_v2_service.this.name
}
