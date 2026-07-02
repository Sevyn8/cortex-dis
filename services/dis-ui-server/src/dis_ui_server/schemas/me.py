"""Wire model for ``GET /me/roles`` (the caller's resolved DIS role strings).

DIS step 2 (frontend enablement): the SPA gates on role strings, but Customer
Master issues no roles claim on the token. This endpoint resolves the caller's
roles DB-side by reusing step 2a's CM client + ``roles_from_grants`` mapping (the
same tuple->role table the ``require_super_admin`` gate uses), so the UI gates and
the server gate agree on a real Auth0 login.

``resolved`` is the fail-safe signal: ``true`` when CM answered (``roles`` is the
authoritative set, possibly empty for a caller with no elevated grants); ``false``
when CM could not be reached / is unset / errored (``roles`` is ``[]`` and the SPA
hides role surfaces while keeping tenant-default surfaces working). This is an
informational self-read, so a CM failure degrades to 200 + ``resolved=false``
rather than a 503 gate denial (see handlers/me.py).
"""

from __future__ import annotations

from pydantic import BaseModel


class MeRolesResponse(BaseModel):
    """The caller's resolved DIS role strings, plus a fail-safe ``resolved`` flag."""

    roles: list[str]
    resolved: bool
