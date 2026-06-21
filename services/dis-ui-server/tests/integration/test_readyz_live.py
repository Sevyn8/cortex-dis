"""``/readyz`` against the live stack (slice acceptance criterion 2).

Two halves of the foundation proof:

1. The service role on the live DIS database: a tenant-scoped session opens,
   the FORCE-RLS probe query runs → 200 ready.
2. The ADMIN role (SUPERUSER/BYPASSRLS): connectivity is perfect and the role
   HAS the table grant (live-verified: ``has_table_privilege`` is true), yet
   readiness reports 503 — and the test asserts the 503 came SPECIFICALLY from
   the dis-rls posture guard (``RlsContextError`` captured on the readyz log
   path), not from unreachability or a missing grant. Readiness validates the
   ISOLATION path, not mere connectivity.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from dis_core.errors import RlsContextError
from dis_ui_server.config import SERVICE_NAME
from dis_ui_server.main import create_app

pytestmark = pytest.mark.integration


def test_readyz_ready_on_the_live_stack(stack_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_URL", stack_env["POSTGRES_URL"])
    with TestClient(create_app()) as client:
        response = client.get("/readyz")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ready"}


class _CaptureHandler(logging.Handler):
    """Captures records off the SERVICE logger directly.

    pytest's ``caplog`` cannot be used here: ``create_app`` calls dis-core
    ``configure_logging``, which REPLACES the root logger's handler list
    (``root.handlers = [handler]``, logging.py) and thereby evicts caplog's
    root-attached capture handler. The named service logger's own handler list
    is never touched, so a handler attached there sees every readyz record.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_readyz_degrades_for_a_bypassing_role(
    stack_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same database, same network, granted SELECT — only the POSTURE differs:
    # the admin role can bypass RLS, so the rls_session guard must refuse it.
    monkeypatch.setenv("POSTGRES_URL", stack_env["POSTGRES_ADMIN_URL"])
    capture = _CaptureHandler()
    service_logger = logging.getLogger(SERVICE_NAME)
    service_logger.addHandler(capture)
    try:
        with TestClient(create_app()) as client:
            response = client.get("/readyz")
            assert response.status_code == 503
            assert response.json() == {"status": "degraded"}
            # Liveness is unaffected by the refused posture.
            assert client.get("/healthz").status_code == 200
    finally:
        service_logger.removeHandler(capture)

    # The RIGHT reason: the 503 path logged the dis-rls guard's RlsContextError
    # (a dead DB would surface OSError/OperationalError; a missing grant a
    # ProgrammingError — either would fail these assertions).
    guard_errors = [
        record.exc_info[1]
        for record in capture.records
        if record.exc_info is not None and isinstance(record.exc_info[1], RlsContextError)
    ]
    assert guard_errors, "503 must come from the dis-rls posture guard, not any other failure"
    assert any("bypass" in str(err) for err in guard_errors)
