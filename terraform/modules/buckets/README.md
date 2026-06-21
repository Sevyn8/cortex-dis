# Module: buckets

The bronze, onboarding-staging, and upload GCS buckets. Uniform bucket-level access + enforced public access prevention. Versioning on, with a lifecycle rule deleting object versions beyond the newest 3 (staging cost control). Bucket names are globally unique; adjust the suffix if a name is taken.
