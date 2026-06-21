"""V4 signed-URL issuance for tenant-direct uploads.

A receiver issues a signed PUT URL scoped to exactly one object path (Slice 8: a
15-minute URL from ``dis-ui-server``); the tenant PUTs the bytes directly to GCS. The
URL is scoped to the single issued path — never a wildcard (lib CLAUDE.md rule).

Signing is **deterministic and offline**: V4 signing uses the signer credential's
private key locally, with no network round-trip, so issuance is unit-testable without
the emulator. A well-formed URL is *not* proof that real GCS accepts the signature —
that is unverified until a real-GCS slice (first use: Slice 8). Tests pass a throwaway
test signer credential only, never a real service account.
"""

from __future__ import annotations

from datetime import timedelta

from google.auth.credentials import Credentials
from google.cloud import storage

from dis_core.errors import StorageError

# GCS V4 signed URLs cannot live longer than 7 days.
_MAX_EXPIRES_SECONDS = 7 * 24 * 60 * 60


def generate_upload_url(
    object_path: str,
    *,
    bucket: str,
    expires_seconds: int,
    credentials: Credentials,
    content_type: str | None = None,
    project: str | None = None,
) -> str:
    """Return a V4 signed PUT URL scoped to exactly ``object_path``.

    ``credentials`` must carry a local signer (e.g. a service-account credential).
    Raises :class:`StorageError` for an empty path/bucket or an out-of-range expiry.
    Performs no network I/O.
    """
    if not object_path:
        raise StorageError("cannot sign an empty object path", object_path=object_path)
    if not bucket:
        raise StorageError("cannot sign without a bucket name", object_path=object_path)
    if not 0 < expires_seconds <= _MAX_EXPIRES_SECONDS:
        raise StorageError(
            f"expires_seconds must be in (0, {_MAX_EXPIRES_SECONDS}]; got {expires_seconds}",
            object_path=object_path,
        )

    client = storage.Client(project=project or "dis-local", credentials=credentials)
    blob = client.bucket(bucket).blob(object_path)
    url: str = blob.generate_signed_url(  # untyped client boundary -> declared str
        version="v4",
        expiration=timedelta(seconds=expires_seconds),
        method="PUT",
        content_type=content_type,
    )
    return url
