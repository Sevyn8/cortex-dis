# Module: secrets

Creates Secret Manager CONTAINERS only; values are set out of band (gcloud or console), never in Terraform. The one exception is the DIS DB app-user password, which is GENERATED (random_password) and stored as a secret version so there is no hardcoded password. It lives in Terraform state (in the private state bucket), not in code.
