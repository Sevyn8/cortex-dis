# Module: project-services

Enables the GCP APIs DIS needs (compute, servicenetworking, sqladmin, run, pubsub, secretmanager, storage, artifactregistry, cloudbuild, iam, vpcaccess). Apply this first; other modules depend on it. disable_on_destroy is false so tearing down DIS does not disable APIs other workloads might use.
