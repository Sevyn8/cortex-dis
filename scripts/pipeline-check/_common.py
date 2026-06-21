"""Shared helpers for the pipeline-check operator scripts (Script A + Script B).

This module is operator tooling under ``scripts/`` — it calls the real DIS HTTP
endpoints, mints a LOCAL dev-stub JWT, and reads the DB read-only to observe
landing. It NEVER writes to canonical/pipeline tables and NEVER modifies any
service/lib/schema code.

Run via ``uv run python`` so the workspace (incl. ``dis_testing.fixtures``,
``httpx``, ``pyjwt``, ``psycopg``) is importable.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import jwt
import psycopg

from dis_testing.fixtures import PRIMARY_STORE, PRIMARY_TENANT

# --- Paths --------------------------------------------------------------------
# scripts/pipeline-check/_common.py -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "scripts" / "pipeline-check"
LOCAL_DIR = PKG_DIR / "local"  # gitignored (see .gitignore)
INPUTS_DIR = LOCAL_DIR / "inputs"
OUT_DIR = LOCAL_DIR / "out"


def _load_dotenv() -> None:
    """Populate missing env vars from the repo-root .env (e.g. POSTGRES_URL).

    The services load .env via their own config; these standalone scripts don't
    inherit that, so we fill any UNSET keys from .env without overriding the
    shell. Minimal KEY=VALUE parser — no dependency on python-dotenv.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# --- Dev-stub JWT parameters --------------------------------------------------
# MUST stay byte-identical to the verifier's constants
# (services/dis-ui-server/src/dis_ui_server/auth/verifier.py:32-35). This is the
# dev-stub seam; when auth moves to real Customer Master Auth0/JWKS (D25), this
# mint helper is the single thing that must change.
DEV_STUB_SECRET = "dis-ui-dev-stub-secret-not-for-production"
DEV_STUB_ISSUER = "https://customer-master.local"
DEV_STUB_AUDIENCE = "dis"
DEV_STUB_ALGORITHM = "HS256"

DEFAULT_BASE_URL = os.environ.get("DIS_UI_BASE", "http://localhost:8080")


# --- Seeded fixture identity (same source make seed uses) ---------------------
def seeded_tenant_uuid() -> str:
    """The local seeded tenant UUID — read from the SAME fixture make seed uses,
    so the minted token's tenant and the seeded tenant can never drift."""
    return str(PRIMARY_TENANT.uuid)


def seeded_store_code() -> str:
    """The local seeded ACTIVE store code (PRIMARY_STORE)."""
    assert PRIMARY_STORE.store_code is not None  # PRIMARY_STORE is the ACTIVE TX-101
    return str(PRIMARY_STORE.store_code)


# --- JWT minting --------------------------------------------------------------
def mint_tenant_token(tenant_uuid: str, *, sub: str = "pipeline-check", ttl_days: int = 1) -> str:
    """Mint a LOCAL dev-stub TENANT token for ``tenant_uuid``.

    Mirrors the signing recipe in scripts/jwt/gen_dis_jwt_90d_v1.sh:83-110. The
    verifier requires ``user_type``; a TENANT token must carry ``tenant_id``.
    """
    now = dt.datetime.now(tz=dt.UTC)
    payload = {
        "sub": sub,
        "iss": DEV_STUB_ISSUER,
        "aud": DEV_STUB_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(days=ttl_days)).timestamp()),
        "user_type": "TENANT",
        "tenant_id": tenant_uuid,
        "roles": ["dis:ops", "dis:read", "dis:upload", "dis:mapping_admin"],
    }
    return jwt.encode(payload, DEV_STUB_SECRET, algorithm=DEV_STUB_ALGORITHM)


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Stack readiness (reuse run_dis_on_local, do not reinvent) ----------------
def _readyz_ok(base: str) -> bool:
    try:
        return httpx.get(f"{base}/readyz", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_stack(*, skip: bool = False, base: str = DEFAULT_BASE_URL, verbose: bool = False) -> None:
    """Bring the local stack up (or reuse it) via the existing orchestrator.

    ``scripts/run_dis_on_local start`` is idempotent: it re-runs ``make run-local``
    cleanly, reuses already-running residents, runs ``make check`` + seed +
    mirror-sync, and starts dis-ui-server + the two workers.

    Readiness is judged by dis-ui-server's ``/readyz`` probe, NOT by the
    orchestrator's exit code. The orchestrator's ~30-line block is CAPTURED and
    collapsed to a single "stack ready" line on success; the full block is shown
    only on failure or when ``verbose`` is set, so it no longer dominates a run.
    """
    if skip:
        if not _readyz_ok(base):
            raise SystemExit(f"--no-stack but {base}/readyz is not 200; bring the stack up first")
        print("✓ stack ready (--no-stack; operator-managed)")
        return
    script = REPO_ROOT / "scripts" / "run_dis_on_local"
    print("· bringing stack up (run_dis_on_local; reuses running services)…")
    proc = subprocess.run(
        [str(script), "start"], cwd=str(REPO_ROOT), capture_output=True, text=True
    )  # exit code intentionally ignored — readiness is judged by /readyz below
    ready = False
    for _ in range(20):
        if _readyz_ok(base):
            ready = True
            break
        time.sleep(1.0)
    if verbose or not ready:
        # Surface the orchestrator's full block — on demand, or to diagnose a failure.
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="")
    if not ready:
        raise SystemExit(
            f"stack not ready: {base}/readyz never returned 200 after run_dis_on_local start. "
            "Check /tmp/dis-run-logs/ or pass --no-stack if you manage the stack yourself."
        )
    print("✓ stack ready (run_dis_on_local)")


# --- Read-only DB observation (RLS-scoped) ------------------------------------
def _psycopg_dsn() -> str:
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise SystemExit("POSTGRES_URL is not set (see .env / docs/local-setup.md)")
    # psycopg3 wants a plain libpq URL; strip the SQLAlchemy +psycopg driver tag.
    return url.replace("postgresql+psycopg://", "postgresql://")


@contextmanager
def rls_session(tenant_uuid: str) -> Iterator[psycopg.Cursor[Any]]:
    """A read-only cursor with the two RLS GUCs set for ``tenant_uuid``.

    canonical.*, quarantine.* and audit.events are all FORCE RLS and
    ithina_dis_user is NOBYPASSRLS, so BOTH GUCs (app.user_type, app.tenant_id)
    must be set or every SELECT silently returns zero rows. Mirrors
    libs/dis-rls/src/dis_rls/session.py and libs/dis-testing seed.py. Never
    commits — observation only.
    """
    with psycopg.connect(_psycopg_dsn(), autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.user_type', 'TENANT', true)")
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_uuid,))
                yield cur
        finally:
            conn.rollback()  # read-only posture: discard the (SELECT-only) transaction


def rls_query(tenant_uuid: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """One read-only RLS-scoped query → list of rows."""
    with rls_session(tenant_uuid) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
