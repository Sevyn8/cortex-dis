"""Thin FastAPI routers, one per UI sub-module; ``main.py`` mounts them.

Slice 13a ships ``health.py`` only. Later handlers (upload_session, mapping
CRUD, quarantine, audit, …) attach under the ``/api/v1`` router; the probes
stay at the root.
"""
