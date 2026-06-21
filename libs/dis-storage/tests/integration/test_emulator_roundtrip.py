"""GCS object access through the wrapper, against the local emulator (AC6 part b).

Marked ``integration``: needs ``STORAGE_EMULATOR_HOST`` (fake-gcs-server). The
PUT/GET round-trip *through a signed URL* is a separate, emulator-dependent sub-check
that is explicitly deferred to real GCS (see ``test_signed_url_roundtrip_deferred``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from dis_core.ids import new_uuid7

if TYPE_CHECKING:  # runtime import stays lazy inside the fixture
    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration


# AC6(b) is stack-dependent: it must NOT silently skip when the emulator is absent
# (a stackless run would report green without object access ever being exercised). So
# a missing/unreachable emulator is a loud ERROR, not a skip. Scope: this slice's
# integration tests only.
class StackRequiredError(RuntimeError):
    """The GCS emulator is required for this test but is absent."""


@pytest.fixture
def emulator_client() -> StorageClient:
    host = os.environ.get("STORAGE_EMULATOR_HOST")
    if not host:
        raise StackRequiredError(
            "STORAGE_EMULATOR_HOST is not set — the GCS round-trip test refuses to skip "
            "silently. Bring up the stack (make run-local)."
        )

    from google.api_core.exceptions import Conflict

    from dis_storage.client import StorageClient

    bucket = "dis-storage-test"
    try:
        sc = StorageClient(bucket=bucket)
        # fake-gcs-server needs the bucket to exist; create it idempotently.
        try:
            sc._client.create_bucket(bucket)
        except Conflict:
            pass
    except Exception as exc:  # noqa: BLE001 — emulator unreachable → ERROR loudly, never skip
        raise StackRequiredError(
            f"GCS emulator at {host} unreachable ({exc!r}); refusing to skip. "
            "Bring up the stack (make run-local)."
        ) from exc
    return sc


def test_wrapper_upload_download_roundtrip(emulator_client: StorageClient) -> None:
    object_path = f"tenant/t-test/source/manual_csv_upload/yyyy=2026/mm=06/dd=03/{new_uuid7()}.csv"
    payload = b"sku,units_sold\nA1,5\n"

    emulator_client.upload_bytes(object_path, payload, content_type="text/csv")
    assert emulator_client.download_bytes(object_path) == payload


@pytest.mark.xfail(
    reason="PUT/GET *through* a V4 signed URL is deferred to real GCS: fake-gcs-server "
    "does not reliably honour signed-URL signatures. Issuance correctness and "
    "wrapper object access (the two checks above/in unit tests) still hold. "
    "NOTE: Slice 8 (the originally planned first consumer) SUPERSEDED the "
    "signed-URL upload mechanic with synchronous stream-through, so signed URLs "
    "currently have NO consumer — this stays deferred until a slice actually "
    "issues one against real GCS.",
    strict=False,
)
def test_signed_url_roundtrip_deferred() -> None:
    # Intentionally not implemented against the emulator; named so the deferral is
    # explicit rather than a silent gap.
    raise AssertionError("signed-URL round-trip deferred to real GCS (no live consumer)")
