"""Create the DIS Pub/Sub topics and worker subscriptions on the local emulator.

Idempotent: existing topics/subscriptions are skipped. Refuses to run against real
GCP. This is the ONE local provisioning place (Slice 9b): worker runtime code never
creates its own subscription — an absent subscription is a loud startup error in
the worker, not a silent auto-repair.

The topic/subscription NAME set lives in ``dis_core.pubsub_names`` (the single
source both this tool and the dis-testing pytest harness provision from, so they
can never drift on names); this module is the ``make topics-create`` CLI wrapper.
"""

import os
import sys

from dis_core.pubsub_names import provision_pubsub


def main() -> int:
    if not os.getenv("PUBSUB_EMULATOR_HOST"):
        print("PUBSUB_EMULATOR_HOST not set; refusing to run against real Pub/Sub.", file=sys.stderr)
        return 2

    project = os.getenv("PUBSUB_PROJECT_ID", "local-dis")
    provision_pubsub(project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
