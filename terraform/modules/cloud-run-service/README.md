# Module: cloud-run-service (reusable)

Deploys a Cloud Run v2 service: image, service account, optional VPC connector (for private Cloud SQL), optional Cloud SQL mount, plain env vars, secret-backed env vars (Secret Manager latest version), scaling (min 0 = scale to zero), and optional public-invoker IAM. Used for all four backend services and the frontend. If a domain-restricted-sharing org policy blocks public invokers, set allow_public = false and front the service with IAP or a load balancer.
