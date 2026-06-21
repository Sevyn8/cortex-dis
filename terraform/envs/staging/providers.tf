provider "google" {
  project = var.project_id
  region  = var.region

  # Applied to every resource that supports labels.
  default_labels = var.labels
}

provider "google-beta" {
  project = var.project_id
  region  = var.region

  default_labels = var.labels
}
