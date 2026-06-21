"""UUIDv7 generation — the single home for new identifiers in DIS.

Root CLAUDE.md hard rule 3: every PK and identifier uses this helper; never
``uuid.uuid4``. UUIDv7 is time-ordered, which keeps index locality good for the
high-volume canonical/event tables.

This is the *client-side* generator (``uuid_utils.uuid7``), returning a stdlib
``uuid.UUID`` so Pydantic and SQLAlchemy treat it as an ordinary UUID. The
Postgres ``public.uuidv7()`` function (Slice 1) is the *server-side* default that
stamps DB-generated PKs; the two are independent implementations that both emit
valid version-7 UUIDs. Use this helper for anything generated in Python
(``trace_id``, client-minted ids); let the DB default handle server-side PKs.
"""

from __future__ import annotations

from uuid import UUID

import uuid_utils


def new_uuid7() -> UUID:
    """Return a fresh time-ordered UUIDv7 as a stdlib :class:`uuid.UUID`."""
    # uuid_utils.uuid7() returns its own UUID type; normalise to stdlib UUID so
    # downstream (Pydantic, psycopg) sees a plain uuid.UUID.
    return UUID(bytes=uuid_utils.uuid7().bytes)
