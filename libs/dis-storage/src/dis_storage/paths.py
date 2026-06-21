"""The frozen canonical GCS object-path scheme (root CLAUDE.md hard rule 9).

    tenant/{tenant_uuid}/source/{source_id}/yyyy={Y}/mm={M}/dd={D}/{trace_id}.{ext}

Tenant prefix first (the security boundary), source second (operational queries),
date partitioning last (lifecycle rules). The tenant segment is the **internal
tenant UUID** (lowercase hex 8-4-4-4-12, decisions.md D53) — never an external
code, because the UUID is immutable while external codes are user-editable.
Confirmed against the ``gcs_uri`` regex in the frozen Pub/Sub contracts. Never
improvise another shape.

The caller supplies ``trace_id`` (hard rule 4: the lib never mints it) and it is
echoed verbatim, both on build and on parse. The date segments come from
``event_ts``, normalised to UTC so partitioning is stable regardless of the
caller's timezone.

This module is the only place object paths are built **or parsed** (hard rule 9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from dis_core.errors import StorageError
from dis_core.timestamps import ensure_utc

# The canonical shape, mirroring the contract `gcs_uri` regex (minus scheme+bucket).
_PATH_RE = re.compile(
    r"^tenant/(?P<tenant>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/source/(?P<source>[^/]+)"
    r"/yyyy=(?P<yyyy>[0-9]{4})/mm=(?P<mm>[0-9]{2})/dd=(?P<dd>[0-9]{2})"
    r"/(?P<filename>[^/]+)$"
)

# gs://{bucket}/{object key}. The bucket char-class matches the contract `gcs_uri`
# regexes (`[a-z0-9-]+`); the key is everything after the first slash past the bucket
# and is validated by parse_object_path, not here.
_URI_RE = re.compile(r"^gs://(?P<bucket>[a-z0-9-]+)/(?P<key>.+)$")


@dataclass(frozen=True)
class ParsedObjectPath:
    """The components of a canonical object path, recovered by :func:`parse_object_path`.

    ``tenant_id`` is the internal tenant UUID (typed; the segment is canonical
    lowercase by construction). ``trace_id`` is the verbatim filename stem — a
    string, never coerced, because the path builder echoes the caller's trace_id
    verbatim (hard rule 4) and the parser must not mangle it.
    """

    tenant_id: UUID
    source_id: str
    year: int
    month: int
    day: int
    trace_id: str
    ext: str


def _coerce_tenant_uuid(raw: str, *, trace_id: str | None) -> str:
    """Canonical lowercase UUID text for the tenant segment, or StorageError.

    The contract ``gcs_uri`` regex admits only lowercase-hex 8-4-4-4-12; coercing
    through :class:`uuid.UUID` guarantees a built path always matches it.
    """
    try:
        return str(UUID(raw))
    except ValueError as exc:
        raise StorageError(
            f"cannot build object path: tenant_id is not a UUID: {raw!r}",
            tenant_id=raw,
            trace_id=trace_id,
        ) from exc


def build_object_path(
    *,
    tenant_id: UUID | str,
    source_id: str,
    trace_id: UUID | str,
    event_ts: datetime,
    ext: str,
) -> str:
    """Build the canonical object path (the key within the bronze bucket).

    The tenant segment is normalised to canonical lowercase UUID text; a
    non-UUID ``tenant_id`` raises :class:`StorageError` (the contract regex
    would reject the path it produced). ``trace_id`` is echoed verbatim.
    Raises :class:`StorageError` if a required identifier is empty. Does not
    mint any identifier and performs no I/O.
    """
    tid_raw = str(tenant_id).strip()
    sid = source_id.strip()
    trace = str(trace_id).strip()
    extension = ext.strip().lstrip(".")

    missing = [
        name
        for name, value in (
            ("tenant_id", tid_raw),
            ("source_id", sid),
            ("trace_id", trace),
            ("ext", extension),
        )
        if not value
    ]
    if missing:
        raise StorageError(
            f"cannot build object path: empty required field(s): {', '.join(missing)}",
            tenant_id=tid_raw or None,
            trace_id=trace or None,
        )

    tid = _coerce_tenant_uuid(tid_raw, trace_id=trace)

    ts = ensure_utc(event_ts)
    return (
        f"tenant/{tid}/source/{sid}/yyyy={ts.year:04d}/mm={ts.month:02d}/dd={ts.day:02d}/{trace}.{extension}"
    )


def parse_object_path(path: str) -> ParsedObjectPath:
    """Recover the components of a canonical object path (inverse of build).

    Accepts the object key only (the value :func:`build_object_path` returns),
    not a full ``gs://bucket/...`` URI — strip the scheme and bucket first.
    The filename splits on its **last** dot: everything before it is the
    verbatim ``trace_id``, after it the ``ext``. Raises :class:`StorageError`
    on any shape mismatch. Performs no I/O.
    """
    candidate = path.strip()
    if candidate.startswith("gs://"):
        raise StorageError(
            "cannot parse object path: got a gs:// URI; pass the object key "
            "(strip the scheme and bucket segment first)",
        )

    match = _PATH_RE.match(candidate)
    if match is None:
        raise StorageError(
            f"cannot parse object path: does not match the canonical scheme: {candidate!r}",
        )

    filename = match["filename"]
    trace, dot, extension = filename.rpartition(".")
    if not dot or not trace or not extension:
        raise StorageError(
            f"cannot parse object path: filename is not '{{trace_id}}.{{ext}}': {filename!r}",
        )

    return ParsedObjectPath(
        tenant_id=UUID(match["tenant"]),
        source_id=match["source"],
        year=int(match["yyyy"]),
        month=int(match["mm"]),
        day=int(match["dd"]),
        trace_id=trace,
        ext=extension,
    )


def split_object_uri(uri: str) -> tuple[str, str]:
    """Split a full ``gs://{bucket}/{object key}`` URI into ``(bucket, object_key)``.

    The Pub/Sub contracts carry ``gcs_uri`` as a full URI, while
    :func:`parse_object_path` (and :class:`~dis_storage.client.StorageClient`) take
    the object key only — this is the sanctioned bridge between the two (hard rule 9:
    paths are split and parsed only here, never hand-split in service code). The key
    is returned verbatim; validating its canonical shape is :func:`parse_object_path`'s
    job. Raises :class:`StorageError` on a non-``gs://`` scheme, a bucket outside the
    contract char-class, or a missing key. Performs no I/O.
    """
    candidate = uri.strip()
    match = _URI_RE.match(candidate)
    if match is None:
        raise StorageError(
            f"cannot split object URI: not a gs://{{bucket}}/{{key}} form: {candidate!r}",
        )
    return match["bucket"], match["key"]
