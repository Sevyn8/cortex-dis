terraform {
  backend "gcs" {
    bucket = "sevyn8-tfstate"
    prefix = "dis/staging"
  }
}
