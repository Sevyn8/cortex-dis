# Module: cloud-sql

Postgres instance with PRIVATE IP ONLY (no public IP), the DIS database, and the app DB user. Schema is owned by Alembic and applied separately; this module does NOT manage schema. deletion_protection is on. The app-user password comes from a generated secret (see the secrets module); prefer Cloud SQL IAM auth for production.
