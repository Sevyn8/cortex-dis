"""The ``/api/v1`` mount point for every UI data endpoint (durable invariant).

The prefix mechanism is a plain ``APIRouter(prefix=API_PREFIX)`` that
``main.py`` includes once; handlers attach their routers here and inherit the
deployed base without restating it. The contract's relative
``/v1/<group>/<resource>`` paths are unchanged (only the deployed base is
``/api/v1``), and dis-ui's ``client.ts`` fetch base must agree when the
frontend's real mode wires up (13b/19, contract Appendix B; flagged to the UI
engineer). Health probes deliberately do NOT live here: ``/healthz`` and
``/readyz`` stay at the root per infra convention.

Slice 14b mounts the first data endpoints: the store list, the field catalog,
and the mapping-template resource. Slice 8 mounts the synchronous CSV upload.
"""

from __future__ import annotations

from fastapi import APIRouter

from dis_ui_server.config import API_PREFIX
from dis_ui_server.handlers import (
    csv_uploads,
    dashboard,
    mapping_suggestions,
    mapping_templates,
    quarantine,
    stores,
    template_mapping_fields,
)

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(stores.router)
api_router.include_router(template_mapping_fields.router)
api_router.include_router(mapping_templates.router)
api_router.include_router(csv_uploads.router)
api_router.include_router(mapping_suggestions.router)
api_router.include_router(quarantine.router)
api_router.include_router(dashboard.router)
