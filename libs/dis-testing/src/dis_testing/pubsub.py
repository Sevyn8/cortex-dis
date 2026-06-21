"""The test-scoped Pub/Sub project (D100 structural isolation).

Integration tests run on a SEPARATE emulator project from the resident data-plane
workers (csv-ingest-worker, streaming-consumer), which stay on ``PUBSUB_PROJECT_ID``
= ``local-dis``. A resident's subscription is bound to its project, so a message
published to the test project is unreachable to it BY CONSTRUCTION — a resident can
never consume a test-published message, regardless of whether it is running. See
docs/decisions.md D100 and docs/scratch/resident-test-isolation-grounding.md.

The plugin (``plugin.py``) sets ``PUBSUB_PROJECT_ID`` to this value for the pytest
process (gated on ``PUBSUB_EMULATOR_HOST``) before any test module imports, so the
in-process services (dis-ui-server's ``create_app``) and the test
publishers/subscribers all resolve the test project from the one env var they
already read.
"""

from __future__ import annotations

import os

# Single definition of the test project name. DIS_TEST_PUBSUB_PROJECT_ID is the
# escape hatch; the default is a distinct namespace from the resident local-dis.
TEST_PUBSUB_PROJECT_ID = os.environ.get("DIS_TEST_PUBSUB_PROJECT_ID", "local-dis-test")


def pubsub_test_project() -> str:
    """The emulator project integration tests publish/subscribe on.

    After the plugin's ``pytest_configure`` override, ``PUBSUB_PROJECT_ID`` already
    holds this value; the fallback keeps module-level reads safe when the override
    did not fire (a bare run with no emulator, where the test skips anyway).
    """
    return os.environ.get("PUBSUB_PROJECT_ID") or TEST_PUBSUB_PROJECT_ID


def pubsub_stack_project() -> str:
    """The project the local STACK runs on — residents (run_dis_on_local) and the
    docker containers (the Customer Master fake, …) all publish/subscribe here.

    The plugin records the pre-override ``PUBSUB_PROJECT_ID`` under
    ``DIS_STACK_PUBSUB_PROJECT_ID`` before redirecting in-process test code to the
    test project, so a test that interoperates with a stack PROCESS (not an
    in-process pipeline) — e.g. asserting the CM fake's ``identity.changed`` publish
    — targets the project that external process actually uses. Such tests are not a
    contamination risk: residents subscribe only to ``csv.received`` /
    ``ingress.ready``, never the stack-only topics consumed here.
    """
    return os.environ.get("DIS_STACK_PUBSUB_PROJECT_ID") or "local-dis"
