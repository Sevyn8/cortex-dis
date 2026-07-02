"""Environment-resolved configuration for the UI server.

Required env (no silent default for a required value, code-quality rule 4 — a
missing one raises ``DisError``; this service deliberately defines no new
config-error class because the Slice-13a dis-core edit is pinned to exactly the
three auth-seam errors; the streaming-consumer precedent applies):

- ``POSTGRES_URL`` — the DIS connection (``ithina_dis_user``). Reused by
  ``dis-rls`` ``create_rls_engine``, which positively asserts
  ``current_database()=='ithina_dis_db'`` and a NOSUPERUSER/NOBYPASSRLS role
  (DIS on 5433, never Customer Master).
- ``GCS_BUCKET_BRONZE`` — the bronze bucket the CSV upload writes to (Slice 8;
  the same env name the csv-ingest-worker cross-checks the published
  ``gcs_uri`` against, so producer and consumer cannot drift).
- ``PUBSUB_PROJECT_ID`` — the Pub/Sub project for the ``csv.received`` publish.

OPTIONAL env (NOT in the required-or-crashloop set):

- ``GEMINI_VERTEX_PROJECT`` / ``GEMINI_VERTEX_LOCATION``: the Vertex AI project and
  location for the mapping-suggestion endpoint. Auth is GCP-native (Application
  Default Credentials from the Cloud Run service account), NOT an API key. BOTH
  UNSET is a normal state: the suggester falls back to the mechanical matcher, so
  missing config must NEVER abort startup (they are read with no raise).
- ``GEMINI_IMPERSONATE_SA``: optional service-account email (gemini-dis) to
  IMPERSONATE for the Vertex calls only. When set (with project+location), the
  suggester impersonates this SA via short-lived credentials; the service still
  runs as its own SA for everything else. Unset -> the ambient ADC (the Cloud Run
  service account) is used directly. Read with no raise.

Resolution happens inside the app lifespan, NOT at import time: a missing
required value aborts startup loudly (crashloop is the correct signal for
misconfiguration), while a present-but-unreachable database must NOT block
startup — the engine is lazy and the first connect happens in ``/readyz``,
which degrades to 503. That split is the liveness/readiness foundation this
slice is built on and is test-pinned.

The Customer Master / Auth0 verifier parameters (13b, D25) ARE config:
``jwt_issuer`` / ``jwt_audience`` / ``jwt_jwks_url`` are resolved here so the
RS256/JWKS verifier (``auth/verifier.py``) can be steered per environment. They
default to the fixed production values (the real Auth0 issuer and the shared
audience) and are env-overridable; the JWKS URL derives from the issuer when
unset. (The retired dev-stub's params were deliberately NOT config, to keep the
two HS256 sides from drifting — that constraint died with the dev stub.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dis_core.errors import DisError
from dis_core.pubsub_names import resolve_pubsub_name

_POSTGRES_URL = "POSTGRES_URL"
_CORS_ALLOWED_ORIGINS = "CORS_ALLOWED_ORIGINS"
_GCS_BUCKET_BRONZE = "GCS_BUCKET_BRONZE"
_PUBSUB_PROJECT_ID = "PUBSUB_PROJECT_ID"
# OPTIONAL (Vertex AI): both unset -> mechanical fallback, never crashloop.
_GEMINI_VERTEX_PROJECT = "GEMINI_VERTEX_PROJECT"
_GEMINI_VERTEX_LOCATION = "GEMINI_VERTEX_LOCATION"
# OPTIONAL: SA to impersonate for Vertex calls only (unset -> ambient ADC).
_GEMINI_IMPERSONATE_SA = "GEMINI_IMPERSONATE_SA"
# Customer Master / Auth0 verifier parameters (13b, D25). Env-overridable per
# environment; the JWKS URL derives from the issuer when unset.
_JWT_ISSUER = "JWT_ISSUER"
_JWT_AUDIENCE = "JWT_AUDIENCE"
_JWT_JWKS_URL = "JWT_JWKS_URL"
# OPTIONAL (Atlas A5): the Customer Master origin the Super Admin gate calls to
# resolve atlas:schema:publish (GET /api/v1/me/permissions). NOT required: unset
# is a normal state for deployments without the Atlas console, so it must never
# abort startup. When unset, require_super_admin fails CLOSED (503 deny), never
# grants. A fixed per-deployment origin, not a secret.
_CM_BASE_URL = "CM_BASE_URL"

# The real Customer Master / Auth0 verifier values. These are fixed
# per-deployment identifiers, not secrets and not placeholders, so the default
# IS the correct production value (unlike a rule-4 "silent default for a required
# value"). They stay env-overridable because the real verifier — unlike the
# retired dev-stub, whose params were deliberately constants to prevent drift —
# must be steerable per environment.
DEFAULT_JWT_ISSUER = "https://sevyn8.us.auth0.com/"  # trailing slash (Auth0 iss form)
DEFAULT_JWT_AUDIENCE = "https://api.cortex.sevyn8.com"  # SHARED with Customer Master

SERVICE_NAME = "dis-ui-server"

# The CSV-upload Phase 1 publish target. The contract name (hard rule 10) is
# "csv.received" and remains the default, so local dev (provisioned by
# tools/local/create_topics.py, no env set) is unchanged. Deployment overrides via
# CSV_RECEIVED_TOPIC with the actually-provisioned short name (terraform sources it
# from the pubsub module output, so app and infra cannot drift).
CSV_RECEIVED_TOPIC = resolve_pubsub_name("CSV_RECEIVED_TOPIC", "csv.received")

# The Slice 8 upload ceiling (a decision value, not deployment config): the
# synchronous-streaming-upload register entry's rationale is that 10 MB removes
# the large-file case for direct-to-GCS. Enforced MID-STREAM in upload_stream.py
# (the spoofable Content-Length early-reject is only the cheap first check).
CSV_UPLOAD_MAX_FILE_BYTES = 10 * 1024 * 1024

# The raw-body ceiling = the file limit + an allowance for multipart framing
# (boundaries, part headers, the small template_id/store_code fields). Anything
# past this is rejected mid-stream regardless of how the parts are arranged.
CSV_UPLOAD_BODY_CEILING_BYTES = CSV_UPLOAD_MAX_FILE_BYTES + 64 * 1024

# Atlas console (A4): the multi-file example-export upload. The single-file
# streaming reader (upload_stream.read_csv_upload) is single-part by design and
# not reusable for a set, so the Atlas upload uses FastAPI UploadFile with an
# explicit count cap here and the per-file byte ceiling reused from
# CSV_UPLOAD_MAX_FILE_BYTES above. A vertical's example exports are few.
ATLAS_MAX_UPLOAD_FILES = 10

# The browser-served dis-ui SPA's dev origin (Slice 14c, confirmed live: dis-ui
# runs Vite with NO server.port override and its README pins
# "pnpm dev - dev server on http://localhost:5173"). NEVER a wildcard: a
# permissive dev posture must not be expressible by default; deployed origins
# are set per environment via CORS_ALLOWED_ORIGINS.
_DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)

# Every UI data endpoint mounts under this prefix (durable invariant, recorded
# in this service's CLAUDE.md); health probes stay at the root. The contract's
# relative /v1/<group>/<resource> paths are unchanged — only the deployed base
# shifts, and dis-ui's client.ts fetch base must agree when real mode wires up
# (13b/19, contract Appendix B).
API_PREFIX = "/api/v1"


@dataclass(frozen=True)
class UiServerConfig:
    """Resolved environment profile for one server process."""

    postgres_url: str
    gcs_bucket_bronze: str
    pubsub_project_id: str
    # Customer Master / Auth0 verifier params (13b, D25): the RS256/JWKS verifier
    # reads these. Always resolved (production defaults, env-overridable); the
    # JWKS URL derives from the issuer when its env is unset.
    jwt_issuer: str
    jwt_audience: str
    jwt_jwks_url: str
    # OPTIONAL Vertex AI config (see module docstring); never required. Both unset -> fallback.
    gemini_vertex_project: str | None = None
    gemini_vertex_location: str | None = None
    # OPTIONAL: SA to impersonate for Vertex calls only; unset -> ambient ADC.
    gemini_impersonate_sa: str | None = None
    # OPTIONAL (Atlas A5): Customer Master origin for the Super Admin permission
    # resolution. Unset -> the super-admin gate fails closed (503), never grants.
    cm_base_url: str | None = None

    @classmethod
    def from_env(cls) -> UiServerConfig:
        """Resolve from the environment, raising on any missing required value."""
        postgres_url = os.environ.get(_POSTGRES_URL)
        if not postgres_url:
            raise DisError(
                f"{_POSTGRES_URL} is not set; cannot reach the DIS database for the "
                "tenant-scoped readiness probe or any later data endpoint"
            )
        gcs_bucket_bronze = os.environ.get(_GCS_BUCKET_BRONZE)
        if not gcs_bucket_bronze:
            raise DisError(
                f"{_GCS_BUCKET_BRONZE} is not set; the CSV upload cannot build or "
                "write the canonical bronze object path"
            )
        pubsub_project_id = os.environ.get(_PUBSUB_PROJECT_ID)
        if not pubsub_project_id:
            raise DisError(
                f"{_PUBSUB_PROJECT_ID} is not set; the CSV upload cannot publish {CSV_RECEIVED_TOPIC!r}"
            )
        # Auth0/CM verifier params: production defaults (the correct fixed values,
        # env-overridable), with the JWKS URL derived from the issuer when unset —
        # the issuer already carries the trailing slash Auth0 uses.
        jwt_issuer = os.environ.get(_JWT_ISSUER) or DEFAULT_JWT_ISSUER
        jwt_audience = os.environ.get(_JWT_AUDIENCE) or DEFAULT_JWT_AUDIENCE
        jwt_jwks_url = os.environ.get(_JWT_JWKS_URL) or f"{jwt_issuer}.well-known/jwks.json"
        # OPTIONAL: read with no raise. Both unset -> the suggester uses the mechanical
        # fallback; missing Vertex config must never abort startup (FM1/FM2).
        gemini_vertex_project = os.environ.get(_GEMINI_VERTEX_PROJECT) or None
        gemini_vertex_location = os.environ.get(_GEMINI_VERTEX_LOCATION) or None
        gemini_impersonate_sa = os.environ.get(_GEMINI_IMPERSONATE_SA) or None
        # OPTIONAL: read with no raise. Unset -> the Atlas super-admin gate fails
        # closed; missing CM config must never abort startup for the rest of the service.
        cm_base_url = os.environ.get(_CM_BASE_URL) or None
        return cls(
            postgres_url=postgres_url,
            gcs_bucket_bronze=gcs_bucket_bronze,
            pubsub_project_id=pubsub_project_id,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
            jwt_jwks_url=jwt_jwks_url,
            gemini_vertex_project=gemini_vertex_project,
            gemini_vertex_location=gemini_vertex_location,
            gemini_impersonate_sa=gemini_impersonate_sa,
            cm_base_url=cm_base_url,
        )


def cors_allowed_origins_from_env() -> tuple[str, ...]:
    """The CORS origin allow-list, comma-separated from ``CORS_ALLOWED_ORIGINS``.

    Resolved at APP-BUILD time in ``create_app`` — not in the lifespan like
    ``UiServerConfig.from_env`` — because Starlette middleware must be
    registered before startup, while the lifespan-resolves-config posture is
    test-pinned for the crashloop-on-missing-POSTGRES_URL split. Same mechanism
    (env read in this module), different resolution point; safe at import
    because the value has a sanctioned default (the slice contract mandates the
    confirmed dis-ui dev origin) and so can never abort an import.

    Unset → the dev default. Set-but-empty is an ambiguous declaration and
    raises (code-quality rule 4): unset it for the default, or list explicit
    origins.
    """
    raw = os.environ.get(_CORS_ALLOWED_ORIGINS)
    if raw is None:
        return _DEFAULT_CORS_ORIGINS
    origins = tuple(origin.strip() for origin in raw.split(",") if origin.strip())
    if not origins:
        raise DisError(
            f"{_CORS_ALLOWED_ORIGINS} is set but contains no origins; unset it for "
            "the dev default or list explicit comma-separated origins"
        )
    return origins
