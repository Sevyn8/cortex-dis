"""GCS client wrapper — the single place GCS objects are read and written.

Honours ``STORAGE_EMULATOR_HOST`` (``.env`` → ``http://localhost:4443``,
fake-gcs-server): when set, the client points at the emulator with anonymous
credentials so local tests need no real GCP. Wrapping the client in the lib gives one
place to enforce the emulator routing and, later, observability and retry policy
(README rationale). Direct ``google-cloud-storage`` import elsewhere is forbidden
(hard rule 9).
"""

from __future__ import annotations

import os

from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from dis_core.errors import StorageError
from dis_core.logging import get_logger

_SERVICE = "dis-storage"
_log = get_logger(_SERVICE)


def _build_client(project: str | None) -> storage.Client:
    """Construct the underlying client, routed to the emulator when configured."""
    emulator_host = os.environ.get("STORAGE_EMULATOR_HOST")
    if emulator_host:
        # Anonymous creds + explicit endpoint: fake-gcs-server, no real GCP auth.
        return storage.Client(
            project=project or "dis-local",
            credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]  # google.auth ships this ctor untyped
            client_options={"api_endpoint": emulator_host},
        )
    # Real GCS: application-default credentials (resolved lazily; no I/O here).
    return storage.Client(project=project)


class StorageClient:
    """Thin wrapper bound to one bucket for object read/write."""

    def __init__(
        self, *, bucket: str, project: str | None = None, client: storage.Client | None = None
    ) -> None:
        if not bucket:
            raise StorageError("StorageClient requires a non-empty bucket name")
        self.bucket_name = bucket
        self._client = client or _build_client(project)

    def upload_bytes(self, object_path: str, data: bytes, *, content_type: str | None = None) -> None:
        """Write ``data`` to ``object_path`` in the bound bucket."""
        blob = self._client.bucket(self.bucket_name).blob(object_path)
        blob.upload_from_string(data, content_type=content_type)
        _log.bind(stage="upload").debug("object written", extra={"object_path": object_path})

    def download_bytes(self, object_path: str) -> bytes:
        """Read and return the bytes at ``object_path`` in the bound bucket."""
        blob = self._client.bucket(self.bucket_name).blob(object_path)
        data: bytes = blob.download_as_bytes()  # untyped client boundary -> declared bytes
        return data
