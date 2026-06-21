"""Environment-resolved connection profile for the Mirror Sync run.

Two Postgres instances, two env-resolved DSNs, **no hard-coded instance** (slice
constraint / read contract §10):

- ``CM_DB_URL`` — the Customer Master read connection. Local: the local CM Postgres
  (port 5432). Cloud: the CM **read replica** via Cloud SQL Auth Proxy / IAM. The
  expected CM database *name* is asserted (``CM_DB_NAME``) so a target mix-up fails
  before any read; the instance/host is never hard-coded.
- ``POSTGRES_URL`` — the DIS write connection (``ithina_dis_user``). Reused by
  ``dis-rls`` ``create_rls_engine``, which asserts ``current_database()=='ithina_dis_db'``.

No silent default for a required value (code-quality rule 4): a missing ``CM_DB_URL`` or
``POSTGRES_URL`` raises. ``CM_DB_NAME`` has a canonical default (``ithina_platform_db``)
shared by the local DB and the cloud replica — it is an assertion target, not a secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dis_core.errors import MirrorSyncError

_CM_DB_URL = "CM_DB_URL"
_POSTGRES_URL = "POSTGRES_URL"
_CM_DB_NAME = "CM_DB_NAME"

# The Customer Master database name (local and the cloud read replica share it).
DEFAULT_CM_DB_NAME = "ithina_platform_db"
# The DIS database — the only sanctioned write target (dis-rls hard-asserts it too).
DIS_DB_NAME = "ithina_dis_db"


@dataclass(frozen=True)
class MirrorSyncConfig:
    """Resolved connection profile for one Mirror Sync run."""

    cm_db_url: str
    dis_db_url: str
    cm_db_name: str = DEFAULT_CM_DB_NAME
    dis_db_name: str = DIS_DB_NAME

    @classmethod
    def from_env(cls) -> MirrorSyncConfig:
        """Resolve the profile from the environment, raising on a missing required value."""
        cm_db_url = os.environ.get(_CM_DB_URL)
        if not cm_db_url:
            raise MirrorSyncError(
                f"{_CM_DB_URL} is not set; cannot reach Customer Master for the DB-pull read"
            )
        dis_db_url = os.environ.get(_POSTGRES_URL)
        if not dis_db_url:
            raise MirrorSyncError(
                f"{_POSTGRES_URL} is not set; cannot reach the DIS database for the mirror write"
            )
        return cls(
            cm_db_url=cm_db_url,
            dis_db_url=dis_db_url,
            cm_db_name=os.environ.get(_CM_DB_NAME, DEFAULT_CM_DB_NAME),
        )
