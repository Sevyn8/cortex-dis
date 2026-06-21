###############################################################################
# buckets: the DIS GCS buckets. Uniform bucket-level access + public access
# prevention (satisfies the uniform-access / public-access org policies).
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

locals {
  # logical name -> suffix
  buckets = {
    bronze             = "bronze"
    onboarding_staging = "onboarding-staging"
    upload             = "upload"
  }
}

resource "google_storage_bucket" "this" {
  for_each = local.buckets

  project  = var.project_id
  name     = "${var.name_prefix}-${each.value}-staging" # globally unique; adjust if taken
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  # Staging lifecycle: clean up old object versions to control cost.
  lifecycle_rule {
    condition {
      num_newer_versions = 3
    }
    action {
      type = "Delete"
    }
  }
}

output "names" {
  value = { for k, b in google_storage_bucket.this : k => b.name }
}

output "urls" {
  value = { for k, b in google_storage_bucket.this : k => b.url }
}
