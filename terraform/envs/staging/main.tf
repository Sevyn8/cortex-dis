###############################################################################
# DIS staging: module wiring.
#
# Durable layer (always applies): project-services, network, service-accounts,
# cloud-sql, buckets, pubsub, secrets, artifact-registry.
#
# Service layer (applies once you set the image_* vars): the Cloud Run services
# and the SFTPGo VM. Each Cloud Run service is guarded so it is skipped while
# its image var is empty, letting you bring the durable layer up first.
###############################################################################

# --- Durable layer ---------------------------------------------------------

module "project_services" {
  source     = "../../modules/project-services"
  project_id = var.project_id
}

module "network" {
  source      = "../../modules/network"
  project_id  = var.project_id
  region      = var.region
  name_prefix = var.name_prefix
  subnet_cidr = var.subnet_cidr

  depends_on = [module.project_services]
}

module "service_accounts" {
  source      = "../../modules/service-accounts"
  project_id  = var.project_id
  name_prefix = var.name_prefix

  depends_on = [module.project_services]
}

module "secrets" {
  source       = "../../modules/secrets"
  project_id   = var.project_id
  secret_names = var.secret_names

  depends_on = [module.project_services]
}

module "cloud_sql" {
  source                     = "../../modules/cloud-sql"
  project_id                 = var.project_id
  region                     = var.region
  name_prefix                = var.name_prefix
  tier                       = var.cloud_sql_tier
  postgres_version           = var.postgres_version
  db_name                    = var.dis_db_name
  db_user                    = var.dis_db_user
  db_user_password           = module.secrets.db_app_password
  network_id                 = module.network.network_id
  private_service_connection = module.network.private_service_connection
}

module "buckets" {
  source      = "../../modules/buckets"
  project_id  = var.project_id
  region      = var.region
  name_prefix = var.name_prefix

  depends_on = [module.project_services]
}

module "pubsub" {
  source      = "../../modules/pubsub"
  project_id  = var.project_id
  name_prefix = var.name_prefix
  topics      = var.pubsub_topics

  depends_on = [module.project_services]
}

module "artifact_registry" {
  source     = "../../modules/artifact-registry"
  project_id = var.project_id
  region     = var.region
  repo_id    = var.artifact_repo_id

  depends_on = [module.project_services]
}

# --- Service layer ----------------------------------------------------------
# Each service is instantiated only when its image var is non-empty.

module "dis_ui_server" {
  source = "../../modules/cloud-run-service"
  count  = var.image_dis_ui_server == "" ? 0 : 1

  project_id               = var.project_id
  region                   = var.region
  name                     = "${var.name_prefix}-ui-server"
  image                    = var.image_dis_ui_server
  service_account_email    = module.service_accounts.emails["ui-server"]
  vpc_connector_id         = module.network.vpc_connector_id
  cloudsql_connection_name = module.cloud_sql.connection_name

  # Grounded in dis-ui-server config.py (the EXACT env the app reads). REQUIRED
  # (the app crashloops on startup without these): POSTGRES_URL, GCS_BUCKET_BRONZE,
  # PUBSUB_PROJECT_ID. POSTGRES_URL is secret-backed (it carries the password); the
  # rest are plain. PUBSUB_EMULATOR_HOST is deliberately UNSET so the publisher uses
  # real Pub/Sub via ambient credentials (D83 emulator-or-ambient).
  env = merge(
    {
      GCS_BUCKET_BRONZE = module.buckets.names["bronze"]
      PUBSUB_PROJECT_ID = var.project_id
      # The CSV-upload publish target, sourced from the pubsub module's provisioned
      # SHORT topic name (dis-csv-received) so the app never drifts from infra. The
      # app defaults to "csv.received" when this is unset (local dev).
      CSV_RECEIVED_TOPIC = module.pubsub.topic_names["csv.received"]
      # Vertex AI (GCP-native auth, no API key). project+location turn the suggester
      # on; GEMINI_IMPERSONATE_SA makes the Vertex calls run as gemini-dis (dis-ui-server
      # keeps its own SA for everything else). All optional: if the grants are not yet
      # in place the suggester degrades to the mechanical fallback, never crashes.
      GEMINI_VERTEX_PROJECT  = var.project_id
      GEMINI_VERTEX_LOCATION = var.gemini_vertex_location
      GEMINI_IMPERSONATE_SA  = var.gemini_dis_sa_email
    },
    # CORS_ALLOWED_ORIGINS raises in the app when SET-BUT-EMPTY, so only set it once
    # the frontend URL is known (14c: explicit origins, no wildcard). Unset -> the
    # app's dev default; empty-string would crashloop.
    var.dis_ui_url == "" ? {} : { CORS_ALLOWED_ORIGINS = var.dis_ui_url }
  )

  # POSTGRES_URL is the full SQLAlchemy URL (carries the db password), so it is
  # secret-backed, not a plain env. The dev-stub JWT verifier reads NO env (real
  # JWKS is slice 13b, deferred), so dis-jwt-jwks is intentionally NOT wired here.
  secret_env = {
    POSTGRES_URL = "dis-database-url"
  }

  # Public Cloud Run + app-level JWT/CORS auth, matching ithina-retail-admin.
  # REQUIRES the project-level iam.allowedPolicyMemberDomains exemption (allowAll);
  # keep false until set, then flip to true.
  allow_public = true
}

module "dis_ui" {
  source = "../../modules/cloud-run-service"
  count  = var.image_dis_ui == "" ? 0 : 1

  project_id            = var.project_id
  region                = var.region
  name                  = "${var.name_prefix}-ui"
  image                 = var.image_dis_ui
  service_account_email = module.service_accounts.emails["ui-frontend"]

  # NO runtime env for the frontend: the UI's mode and API base are BUILD-TIME Vite
  # vars (VITE_DIS_UI_SERVER_MODE + VITE_DIS_UI_SERVER_BASE_URL) baked into the static
  # bundle when the dis-ui IMAGE is built, not read at Cloud Run runtime. So making the
  # hosted frontend call the real backend is an image-BUILD concern, not a Terraform env:
  # build the dis-ui image with the cloudbuild substitutions
  #   _DIS_UI_SERVER_MODE=real
  #   _DIS_UI_SERVER_BASE_URL=<dis-ui-server URL>   (= var.dis_ui_server_url, known after
  #                                                   dis-ui-server deploys; second build)
  # (see terraform/docker/cloudbuild.yaml + dis-ui.Dockerfile). The previous runtime
  # VITE_API_BASE env here was dead: the UI never read it, and a runtime var cannot
  # change an already-built SPA. var.dis_ui_server_url now feeds that build substitution.

  # Public Cloud Run + app-level JWT/CORS auth, matching ithina-retail-admin.
  # REQUIRES the project-level iam.allowedPolicyMemberDomains exemption (allowAll);
  # keep false until set, then flip to true.
  allow_public = true
}

module "csv_ingest_worker" {
  source = "../../modules/cloud-run-service"
  count  = var.image_csv_ingest_worker == "" ? 0 : 1

  project_id               = var.project_id
  region                   = var.region
  name                     = "${var.name_prefix}-csv-ingest-worker"
  image                    = var.image_csv_ingest_worker
  service_account_email    = module.service_accounts.emails["csv-ingest-worker"]
  vpc_connector_id         = module.network.vpc_connector_id
  cloudsql_connection_name = module.cloud_sql.connection_name

  # D58: the query-based dedup is single-instance only, so this worker is pinned to
  # ONE instance. This is a CORRECTNESS constraint (D83), not a scaling preference.
  max_instances = 1
  # Pull-loop worker: keep one instance always alive with CPU always allocated, so the
  # background subscribe loop keeps running when no HTTP request is in flight (only the
  # /healthz probe arrives). Without these a scale-to-zero, CPU-throttled Service would
  # stop consuming when idle.
  min_instances = 1
  cpu_idle      = false

  # Grounded in csv-ingest-worker config.py (REQUIRED: POSTGRES_URL, PUBSUB_PROJECT_ID,
  # GCS_BUCKET_BRONZE). RUN_HEALTH_SERVER=true runs the readiness /healthz on the
  # Cloud-Run-injected $PORT so the Service health check passes (D83). PUBSUB_EMULATOR_HOST
  # unset -> real Pub/Sub via ambient credentials.
  env = {
    PUBSUB_PROJECT_ID = var.project_id
    GCS_BUCKET_BRONZE = module.buckets.names["bronze"]
    RUN_HEALTH_SERVER = "true"
    # The worker's subscription on csv.received and its ingress.ready publish target,
    # sourced from the pubsub module's provisioned SHORT names (dis-csv-received-sub,
    # dis-ingress-ready) so the app never drifts from infra. The app defaults to its
    # local create_topics.py names when these are unset (local dev).
    CSV_RECEIVED_SUBSCRIPTION = module.pubsub.subscription_names["csv.received"]
    INGRESS_READY_TOPIC       = module.pubsub.topic_names["ingress.ready"]
  }
  secret_env = {
    POSTGRES_URL = "dis-database-url"
  }
  allow_public = false
}

module "streaming_consumer" {
  source = "../../modules/cloud-run-service"
  count  = var.image_streaming_consumer == "" ? 0 : 1

  project_id               = var.project_id
  region                   = var.region
  name                     = "${var.name_prefix}-streaming-consumer"
  image                    = var.image_streaming_consumer
  service_account_email    = module.service_accounts.emails["streaming-consumer"]
  vpc_connector_id         = module.network.vpc_connector_id
  cloudsql_connection_name = module.cloud_sql.connection_name

  # Pull-loop worker: keep one instance always alive with CPU always allocated, so the
  # background subscribe loop keeps running when no HTTP request is in flight (only the
  # /healthz probe arrives). max_instances keeps the module default (2); the consumer is
  # concurrency-safe (atomic dual-write, read-time latest-wins).
  min_instances = 1
  cpu_idle      = false

  # Grounded in streaming-consumer config.py (REQUIRED: POSTGRES_URL, PUBSUB_PROJECT_ID,
  # GCS_BUCKET_BRONZE). RUN_HEALTH_SERVER=true runs the readiness /healthz on the
  # Cloud-Run-injected $PORT so the Service health check passes (D83). PUBSUB_EMULATOR_HOST
  # unset -> real Pub/Sub via ambient credentials.
  env = {
    PUBSUB_PROJECT_ID = var.project_id
    GCS_BUCKET_BRONZE = module.buckets.names["bronze"]
    RUN_HEALTH_SERVER = "true"
    # The consumer's subscription on ingress.ready, sourced from the pubsub module's
    # provisioned SHORT name (dis-ingress-ready-sub) so the app never drifts from infra.
    # The app defaults to its local create_topics.py name when this is unset (local dev).
    INGRESS_READY_SUBSCRIPTION = module.pubsub.subscription_names["ingress.ready"]
  }
  secret_env = {
    POSTGRES_URL = "dis-database-url"
  }
  allow_public = false
}

module "mirror_sync_consumer" {
  source = "../../modules/cloud-run-service"
  count  = var.image_mirror_sync_consumer == "" ? 0 : 1

  project_id               = var.project_id
  region                   = var.region
  name                     = "${var.name_prefix}-mirror-sync-consumer"
  image                    = var.image_mirror_sync_consumer
  service_account_email    = module.service_accounts.emails["mirror-sync-consumer"]
  vpc_connector_id         = module.network.vpc_connector_id
  cloudsql_connection_name = module.cloud_sql.connection_name

  env = {
    ENV = "staging"
  }
  secret_env = {
    DIS_DB_PASSWORD       = "dis-db-app-password"
    CM_MIRROR_READER_CONN = "dis-cm-mirror-reader-conn"
  }
  allow_public = false
}

# --- SFTPGo VM (scaffold; refine with Sanjeev) ------------------------------

module "sftpgo" {
  source = "../../modules/sftpgo-vm"

  project_id            = var.project_id
  region                = var.region
  zone                  = var.zone
  name_prefix           = var.name_prefix
  machine_type          = var.sftpgo_machine_type
  network_id            = module.network.network_id
  subnet_id             = module.network.subnet_id
  service_account_email = module.service_accounts.emails["sftpgo"]
  upload_bucket         = module.buckets.names["upload"]
}
