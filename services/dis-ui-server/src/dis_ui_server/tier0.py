"""Tier-0 structural CSV validation (D51/D52) — owned by the upload endpoint.

Structural ONLY: file present (the multipart reader's concern), non-empty,
decodes, parses as CSV, min-rows floor. Column- and mapping-aware checks are
tier 1 (the source-shape suite, downstream of the worker) and are never made
here — this module knows nothing about templates or canonical fields.

Honesty note on "parses as CSV": almost any decoded text tokenises as CSV, so
the load-bearing structural gates are the decode and the min-rows floor; the
``csv.Error`` branch catches the pathological cases (e.g. unterminated quoted
fields spanning the file). The worker's DuckDB preflight re-sniffs downstream
with a real dialect detector (D13/D16) — this gate exists so a structurally
hopeless file is a clean 4xx with no GCS write and no publish.

A failure raises ``UploadStructureError`` (422) with a machine-stable ``reason``
(``empty_file``, ``not_utf8``, ``not_csv``, ``below_min_rows``) and NEVER any
cell value or payload content (hard rule 2 posture).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from dis_core.errors import UploadStructureError

# The floor: a header row plus at least one data row. Fewer carries nothing to
# ingest — a header-only file is a mapping artifact, not data.
MIN_CSV_RECORDS = 2


@dataclass(frozen=True)
class Tier0Result:
    """What the structural pass observed (counts only; never content)."""

    row_count: int  # data rows, excluding the header


def run_tier0(file_bytes: bytes, *, tenant_id: str, trace_id: str) -> Tier0Result:
    """The D51 gate. Raises ``UploadStructureError`` on any structural failure."""
    if not file_bytes or not file_bytes.strip():
        raise UploadStructureError(
            "uploaded file is empty",
            reason="empty_file",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
    try:
        # utf-8-sig: tolerate (and strip) a BOM — Excel exports routinely lead
        # with one and the bytes are otherwise plain UTF-8.
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadStructureError(
            "uploaded file does not decode as UTF-8",
            reason="not_utf8",
            tenant_id=tenant_id,
            trace_id=trace_id,
        ) from exc
    try:
        records = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise UploadStructureError(
            "uploaded file does not parse as CSV",
            reason="not_csv",
            tenant_id=tenant_id,
            trace_id=trace_id,
        ) from exc
    if len(records) < MIN_CSV_RECORDS:
        raise UploadStructureError(
            "uploaded file is below the minimum-rows floor (a header plus at least one data row)",
            reason="below_min_rows",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
    return Tier0Result(row_count=len(records) - 1)
