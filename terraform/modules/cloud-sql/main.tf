###############################################################################
# cloud-sql: Postgres instance (private IP only), the DIS database, and the
# app DB user. Schema is owned by Alembic and applied separately; this module
# does NOT manage schema.
#
# The app user password is generated and written to Secret Manager out of band.
# Here we create the user with a password provided via a variable that the root
# sources from a generated random secret (see secrets module), or you may switch
# to Cloud SQL IAM authentication (preferred) and drop the password entirely.
###############################################################################

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "tier" {
  type = string
}

variable "postgres_version" {
  type = string
}

variable "db_name" {
  type = string
}

variable "db_user" {
  type = string
}

variable "db_user_password" {
  description = "Initial password for the app DB user. Prefer Cloud SQL IAM auth instead; if used, source this from a generated secret, never hardcode."
  type        = string
  sensitive   = true
}

variable "network_id" {
  description = "VPC self link/id for private IP."
  type        = string
}

variable "private_service_connection" {
  description = "The service networking connection id, to enforce ordering."
  type        = string
}

resource "google_sql_database_instance" "this" {
  project          = var.project_id
  name             = "${var.name_prefix}-pg"
  region           = var.region
  database_version = var.postgres_version

  # Prevent accidental destroy of the data plane.
  deletion_protection = true

  depends_on = [var.private_service_connection]

  settings {
    # ENTERPRISE (not the default ENTERPRISE_PLUS) is the edition that allows
    # shared-core tiers like db-f1-micro; keep the cheapest valid staging instance.
    edition           = "ENTERPRISE"
    tier              = var.tier
    availability_type = "ZONAL" # single zone, no HA (staging, smallest)
    disk_size         = 10
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    ip_configuration {
      # Private IP only. No public IP (satisfies sql.restrictPublicIp).
      ipv4_enabled    = false
      private_network = var.network_id
    }

    backup_configuration {
      enabled = true
    }

    database_flags {
      # Helpful for the RLS session GUC pattern the app uses.
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }
}

resource "google_sql_database" "dis" {
  project  = var.project_id
  name     = var.db_name
  instance = google_sql_database_instance.this.name
}

resource "google_sql_user" "app" {
  project  = var.project_id
  name     = var.db_user
  instance = google_sql_database_instance.this.name
  password = var.db_user_password
}

output "instance_name" {
  value = google_sql_database_instance.this.name
}

output "connection_name" {
  description = "INSTANCE connection name for the Cloud SQL connector (project:region:instance)."
  value       = google_sql_database_instance.this.connection_name
}

output "private_ip" {
  value = google_sql_database_instance.this.private_ip_address
}
