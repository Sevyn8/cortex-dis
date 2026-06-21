"""dis-storage — the only sanctioned GCS access path in DIS (root CLAUDE.md hard rule 9).

- :func:`build_object_path` — the frozen canonical object-path scheme (UUID tenant
  segment, decisions.md D53). The caller supplies ``trace_id``; the lib never mints
  it (hard rule 4).
- :func:`parse_object_path` / :class:`ParsedObjectPath` — the inverse: recover the
  path components (tenant UUID typed; trace_id verbatim). The only sanctioned place
  paths are parsed.
- :func:`split_object_uri` — split a contract ``gcs_uri`` (``gs://bucket/key``) into
  ``(bucket, object_key)`` for the two callables above (Slice 9b; consumers must not
  hand-split URIs).
- :class:`StorageClient` — a thin wrapper over ``google.cloud.storage.Client`` that
  honours ``STORAGE_EMULATOR_HOST`` (one place for GCS object read/write).
- :func:`generate_upload_url` — V4 signed PUT URL issuance. Deterministic and offline
  (signing needs a signer credential, not a network round-trip).

Issuance correctness offline is a *well-formed* URL, not proof that real GCS accepts
the signature; that is unverified until a real-GCS slice (first use: Slice 8's
15-minute signed PUT URL handler). Tests sign with a throwaway test credential only.
"""

from __future__ import annotations

from dis_storage.client import StorageClient
from dis_storage.paths import (
    ParsedObjectPath,
    build_object_path,
    parse_object_path,
    split_object_uri,
)
from dis_storage.signed_urls import generate_upload_url

__all__ = [
    "ParsedObjectPath",
    "StorageClient",
    "build_object_path",
    "generate_upload_url",
    "parse_object_path",
    "split_object_uri",
]
