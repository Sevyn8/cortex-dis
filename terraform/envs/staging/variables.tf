###############################################################################
# Core project / location
###############################################################################

variable "project_id" {
  description = "GCP project ID for DIS staging."
  type        = string
  default     = "ithina-retail-dis"
}

variable "project_number" {
  description = "GCP project number (used for some IAM member strings)."
  type        = string
  default     = "1002062049747"
}

variable "region" {
  description = "Primary region for all DIS resources."
  type        = string
  default     = "asia-south1"
}

variable "zone" {
  description = "Zone for zonal resources (the SFTPGo VM)."
  type        = string
  default     = "asia-south1-a"
}

variable "env" {
  description = "Environment name. This project is staging."
  type        = string
  default     = "staging"
}

variable "name_prefix" {
  description = "Prefix for resource names (design convention)."
  type        = string
  default     = "dis"
}

variable "labels" {
  description = "Default labels applied to all resources."
  type        = map(string)
  default = {
    workload    = "dis"
    env         = "staging"
    cost_center = "ithina"
  }
}

###############################################################################
# Networking
###############################################################################

variable "subnet_cidr" {
  description = "Primary subnet CIDR for the DIS VPC."
  type        = string
  default     = "10.20.0.0/24"
}

###############################################################################
# Cloud SQL (Postgres)
###############################################################################

variable "cloud_sql_tier" {
  description = "Cloud SQL machine tier. Smallest viable for staging. NOTE: the legacy db-f1-micro / db-g1-small shared-core tiers are deprecated for newer Postgres versions; the smallest modern tier may be db-custom-1-3840. Confirm for your Postgres version."
  type        = string
  default     = "db-f1-micro"
}

variable "postgres_version" {
  description = "Cloud SQL Postgres version."
  type        = string
  default     = "POSTGRES_16"
}

variable "dis_db_name" {
  description = "The DIS application database name."
  type        = string
  default     = "ithina_dis_db"
}

variable "dis_db_user" {
  description = "The DIS application DB role name (matches infra/local/postgres-init.sql; NOSUPERUSER NOBYPASSRLS in app use)."
  type        = string
  default     = "ithina_dis_user"
}

###############################################################################
# Pub/Sub
###############################################################################

variable "pubsub_topics" {
  description = "Pub/Sub topics to create. Confirm against contracts/pubsub in the app repo."
  type        = list(string)
  default = [
    "csv.received",
    "ingress.ready",
    "ingress.resubmit",
    "identity.changed",
    "mapping.changed",
    "quarantine",
    "pipeline.dlq",
  ]
}

###############################################################################
# Secret Manager (containers only; values set out of band)
###############################################################################

variable "secret_names" {
  description = "Secret Manager secret containers to create. Values are set out of band, never in Terraform."
  type        = list(string)
  default = [
    "dis-db-app-password",
    "dis-cm-mirror-reader-conn",
    "dis-jwt-jwks",
    # The full DIS POSTGRES_URL the apps read directly (dis-ui-server + the two
    # subscriber workers): a SQLAlchemy URL of the form
    # postgresql+psycopg://USER:PASSWORD@/DBNAME?host=/cloudsql/CONNECTION_NAME.
    # Container only; the operator populates the version at deploy with the
    # generated db password (module.secrets.db_app_password) and the cloud-sql
    # connection name. The app reads ONE url, so the secret carries the whole url.
    "dis-database-url",
    # dis-gemini-api-key REMOVED: the mapping-suggester moved to Vertex AI
    # (GCP-native ADC + gemini-dis impersonation, no API key). Nothing reads it.
  ]
}

###############################################################################
# Artifact Registry
###############################################################################

variable "artifact_repo_id" {
  description = "Artifact Registry Docker repo id for DIS service images."
  type        = string
  default     = "dis-images"
}

###############################################################################
# Cloud Run service images
# Set these AFTER you build and push images. Until then a service can be left
# out of the apply (comment its module) or pointed at a placeholder image.
###############################################################################

variable "image_dis_ui_server" {
  description = "Image URI for dis-ui-server (e.g. asia-south1-docker.pkg.dev/ithina-retail-dis/dis-images/dis-ui-server:TAG)."
  type        = string
  default     = ""
}

variable "image_dis_ui" {
  description = "Image URI for the dis-ui frontend static-server container."
  type        = string
  default     = ""
}

variable "image_csv_ingest_worker" {
  description = "Image URI for csv-ingest-worker (Dockerfile TODO)."
  type        = string
  default     = ""
}

variable "image_streaming_consumer" {
  description = "Image URI for streaming-consumer (Dockerfile TODO)."
  type        = string
  default     = ""
}

variable "image_mirror_sync_consumer" {
  description = "Image URI for mirror-sync-consumer (Dockerfile TODO)."
  type        = string
  default     = ""
}

variable "dis_ui_server_url" {
  description = "Public URL of dis-ui-server, used as the frontend API base and for CORS. Known after dis-ui-server deploys; can be set on a second apply."
  type        = string
  default     = ""
}

variable "dis_ui_url" {
  description = "Public URL of the dis-ui frontend, used for dis-ui-server's CORS_ALLOWED_ORIGINS allowlist (14c: explicit origins only, no wildcard). Known after the frontend deploys; set on a second apply."
  type        = string
  default     = ""
}

###############################################################################
# Vertex AI (mapping-suggestions; GCP-native auth, no API key)
###############################################################################

variable "gemini_vertex_location" {
  description = "Vertex AI location for the mapping-suggestion model (GEMINI_VERTEX_LOCATION). Default asia-south1 (Mumbai), verified to serve Gemini on Vertex. Override to us-central1 if the specific model is not available in Mumbai."
  type        = string
  default     = "asia-south1"
}

variable "gemini_dis_sa_email" {
  description = "Email of the gemini-dis service account that dis-ui-server impersonates for Vertex calls (GEMINI_IMPERSONATE_SA). Hand-created out of band, not managed by the service-accounts module. VERIFY this matches the actual SA when the image lands."
  type        = string
  default     = "gemini-dis@ithina-retail-dis.iam.gserviceaccount.com"
}

###############################################################################
# SFTPGo
###############################################################################

variable "sftpgo_machine_type" {
  description = "Machine type for the SFTPGo GCE VM."
  type        = string
  default     = "e2-small"
}
