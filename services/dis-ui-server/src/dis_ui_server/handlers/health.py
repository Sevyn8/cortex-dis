"""Liveness and readiness probes (slice Task 4; probes live at the root).

``/healthz`` is DB-free liveness (contract §2.7): if the process serves HTTP,
it returns 200 — even with the database down or misconfigured-but-parseable.

``/readyz`` is the day-one foundation proof: it opens a tenant-scoped dis-rls
session and runs a policy-evaluated query on a FORCE-RLS table, so 200 means
the ISOLATION PATH works (engine reached ``ithina_dis_db``, the role cannot
bypass RLS, ``app.tenant_id`` scoping applied) — not merely that Postgres
answered. The probe tenant is a fresh UUIDv7 per call: RLS scoping does not
require the tenant to exist, and a never-seen tenant matches ZERO entries of
the ``ix_qr_tenant_*`` btree indexes, so the count is O(log n) descent —
constant work as quarantine fills (operator-confirmed probe-cost posture).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_core.ids import new_uuid7
from dis_core.logging import get_logger
from dis_rls import rls_session
from dis_ui_server.config import SERVICE_NAME

router = APIRouter()

_log = get_logger(SERVICE_NAME)

# A real FORCE-RLS table this service will read (quarantine console), with the
# single-GUC tenant_isolation policy — the strongest probe that needs no seeded
# data (Slice 13a plan, open question 2).
_PROBE_QUERY = text("SELECT count(*) FROM quarantine.quarantined_rows")


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Unauthenticated, DB-free liveness."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: a tenant-scoped dis-rls session opens and a scoped query runs."""
    # The engine rides app.state (lifespan-owned) so tests can build the app
    # against any URL — reachable, dead, or a bypassing role.
    engine: AsyncEngine = request.app.state.engine
    probe_tenant = new_uuid7()
    try:
        async with rls_session(engine, probe_tenant) as conn:
            await conn.execute(_PROBE_QUERY)
    except Exception:
        # Not a swallowed exception (code-quality rule 6): the 503 IS the
        # propagation — readiness reports degraded instead of crashing the
        # probe endpoint. Full context goes to the log; the body stays terse
        # (probes are unauthenticated, internals don't belong in the response).
        _log.bind(stage="readyz", tenant_id=str(probe_tenant)).exception(
            "readiness probe failed: tenant-scoped rls_session did not open"
        )
        return JSONResponse(status_code=503, content={"status": "degraded"})
    return JSONResponse(status_code=200, content={"status": "ready"})
