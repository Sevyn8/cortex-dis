# Module: service-accounts

One least-privilege service account per DIS service (ui-server, csv-ingest-worker, streaming-consumer, mirror-sync-consumer, ui-frontend, sftpgo) plus the project-level IAM bindings each needs. Bindings are project-level for staging simplicity; tighten to per-resource bindings for production.
