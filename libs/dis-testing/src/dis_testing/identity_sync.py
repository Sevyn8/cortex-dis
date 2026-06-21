"""Populate ``identity_mirror`` via the REAL mirror-sync path, for tests.

``identity_mirror`` is owned by mirror-sync (Slice 7, DB-pull); the seeder no
longer writes it. Tests that need FK targets (a mirrored tenant/store) call
:func:`sync_identity_mirror`, which provisions the in-cluster Customer Master
stand-in and runs the actual mirror-sync runner to completion against it — the
same code path production uses, not a shortcut.

Importable without the stack: no module-level connections; the runner is
imported lazily inside the function.
"""

from __future__ import annotations

import os

from dis_testing.customer_master_db import provision_test_cm, reader_url_from

_MANAGED_ENV = ("CM_DB_URL", "POSTGRES_URL", "CM_DB_NAME")


def sync_identity_mirror(admin_url: str, user_url: str) -> None:
    """Provision the test CM stand-in and run mirror-sync into ``identity_mirror``.

    ``admin_url`` is the DIS admin DSN (``POSTGRES_ADMIN_URL``); ``user_url`` is the
    NOBYPASSRLS service-role DSN (``POSTGRES_URL``). Idempotent (the sync is
    upsert-only). Saves and restores any env vars it sets so it never leaks state.
    """
    # 1. Create + seed the stand-in CM (idempotent).
    provision_test_cm(admin_url)

    # 2. Run mirror-sync run-to-completion against the stand-in. The runner reads
    #    CM_DB_URL (read side, pointed at the stand-in via the service role) and
    #    POSTGRES_URL (DIS write side). CM_DB_NAME must be absent so the reader
    #    derives the database from CM_DB_URL.
    saved: dict[str, str | None] = {name: os.environ.get(name) for name in _MANAGED_ENV}
    try:
        os.environ["CM_DB_URL"] = reader_url_from(user_url)
        os.environ["POSTGRES_URL"] = user_url
        os.environ.pop("CM_DB_NAME", None)

        from mirror_sync_consumer.pull.runner import EXIT_OK
        from mirror_sync_consumer.pull.runner import main as run_sync

        exit_code = run_sync()
        assert exit_code == EXIT_OK, f"mirror-sync runner returned {exit_code}, expected {EXIT_OK}"
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior
