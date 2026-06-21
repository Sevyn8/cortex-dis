"""dis-ui-server: the single backend-for-frontend for the DIS UI (D26).

Slice 13a builds the foundation only: the deployable FastAPI skeleton, the
liveness/readiness probes, the auth seam (dev-stub verifier behind
``get_current_identity`` / ``require_tenant`` / ``require_ops``), the dis-rls
per-tenant session wiring + ORM base, the DisError-to-envelope exception
handlers, and structured logging. UI data endpoints land in Slices 8/14+; the
real Customer Master JWKS verifier replaces the dev stub in 13b.
"""
